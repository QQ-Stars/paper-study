import { BusinessError, DecodeError } from './errors';
import type {
  Candidate,
  CitationGraph,
  CitationLink,
  CitationNode,
  Decoder,
  ExplainerPending,
  JobDetail,
  JobRecord,
  JobStatus,
  JobSummary,
  ObsidianLastJob,
  ObsidianPdfMode,
  ObsidianResultCounts,
  ObsidianStatus,
  ObsidianTestResult,
  PaperListItem,
  PaperRecord,
  PdfScan,
  PdfScanFile,
  PdfStatus,
  ReviewCounts,
  ReviewItem,
  ReviewPlan,
  ReviewSnapshot,
  ReviewState,
  Schedule,
  SemanticHit,
  SettingsView,
  StudyStatus,
  TitleTranslationStatus,
  Verification,
} from './types';
import { decodeProcessingJobSummary } from './processingGateway';

function inputObject(value: unknown, path: string): object {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new DecodeError(path, 'object', value);
  }
  return value;
}

function field(value: object, key: string): unknown {
  return Reflect.get(value, key);
}

function assertExactKeys(value: object, keys: readonly string[], path: string): void {
  const expected = new Set(keys);
  const unexpected = Object.keys(value).find((key) => !expected.has(key));
  if (unexpected !== undefined) {
    throw new DecodeError(`${path}.${unexpected}`, 'no unknown field', field(value, unexpected));
  }
  const missing = keys.find((key) => !Object.prototype.hasOwnProperty.call(value, key));
  if (missing !== undefined) {
    throw new DecodeError(`${path}.${missing}`, 'required field', undefined);
  }
}

function at(value: object, key: string, path: string): [unknown, string] {
  return [field(value, key), `${path}.${key}`];
}

export const string: Decoder<string> = (value, path = '$') => {
  if (typeof value !== 'string') throw new DecodeError(path, 'string', value);
  return value;
};

export const number: Decoder<number> = (value, path = '$') => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new DecodeError(path, 'finite number', value);
  }
  return value;
};

export const integer: Decoder<number> = (value, path = '$') => {
  const decoded = number(value, path);
  if (!Number.isInteger(decoded)) throw new DecodeError(path, 'integer', value);
  return decoded;
};

export const boolean: Decoder<boolean> = (value, path = '$') => {
  if (value === true || value === 1) return true;
  if (value === false || value === 0) return false;
  throw new DecodeError(path, 'boolean or SQLite 0|1', value);
};

const strictBoolean: Decoder<boolean> = (value, path = '$') => {
  if (typeof value !== 'boolean') throw new DecodeError(path, 'boolean', value);
  return value;
};

function optionalStrictBoolean(
  value: unknown,
  path: string,
  fallback: boolean,
): boolean {
  return value === undefined ? fallback : strictBoolean(value, path);
}

function optionalString(value: unknown, path: string, fallback: string): string {
  return value === undefined ? fallback : string(value, path);
}

export function arrayOf<T>(decoder: Decoder<T>): Decoder<T[]> {
  return (value, path = '$') => {
    if (!Array.isArray(value)) throw new DecodeError(path, 'array', value);
    return value.map((item, index) => decoder(item, `${path}[${index}]`));
  };
}

export function object(shape: Readonly<Record<string, Decoder<unknown>>>): Decoder<Record<string, unknown>> {
  return (value, path = '$') => {
    const input = inputObject(value, path);
    const result: Record<string, unknown> = {};
    for (const key of Object.keys(shape)) {
      const decoder = shape[key];
      if (decoder) result[key] = decoder(field(input, key), `${path}.${key}`);
    }
    return result;
  };
}

function nullableString(value: unknown, path: string): string | null {
  if (value === undefined || value === null) return null;
  return string(value, path);
}

function nullableNumber(value: unknown, path: string): number | null {
  if (value === undefined || value === null) return null;
  return number(value, path);
}

function nullableInteger(value: unknown, path: string): number | null {
  if (value === undefined || value === null) return null;
  return integer(value, path);
}

function optionalBoolean(value: unknown, path: string, fallback = false): boolean {
  if (value === undefined || value === null) return fallback;
  return boolean(value, path);
}

function count(value: unknown, path: string): number {
  const decoded = integer(value, path);
  if (decoded < 0) throw new DecodeError(path, 'non-negative integer', value);
  return decoded;
}

function oneOf<const T extends readonly string[]>(
  choices: T,
  value: unknown,
  path: string,
): T[number] {
  if (typeof value !== 'string' || !choices.includes(value)) {
    throw new DecodeError(path, choices.join(' | '), value);
  }
  return value;
}

function nullableYear(value: unknown, path: string): string | null {
  if (value === undefined || value === null || value === '') return null;
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return String(value);
  throw new DecodeError(path, 'paper year string or number', value);
}

function studyStatus(value: unknown, path: string, allowMissing = false): StudyStatus {
  if (allowMissing && (value === undefined || value === null || value === '')) return '未开始';
  if (value === '未开始' || value === '学习中' || value === '已理解') return value;
  throw new DecodeError(path, '未开始 | 学习中 | 已理解', value);
}

function reviewState(value: unknown, path: string): ReviewState {
  if (value === 'overdue' || value === 'dueToday' || value === 'upcoming' || value === 'completed') return value;
  throw new DecodeError(path, 'review state', value);
}

function jobStatus(value: unknown, path: string): JobStatus {
  if (value === 'pending' || value === 'running' || value === 'review' || value === 'done' || value === 'failed') return value;
  throw new DecodeError(path, 'pending | running | review | done | failed', value);
}

function stringArray(value: unknown, path: string): string[] {
  if (value === undefined || value === null || value === '') return [];
  let decoded: unknown = value;
  if (typeof value === 'string') {
    try {
      decoded = JSON.parse(value);
    } catch (error) {
      throw new DecodeError(path, 'JSON string containing a string array', error);
    }
  }
  return arrayOf(string)(decoded, path);
}

function csv(value: unknown, path: string): string[] {
  if (value === undefined || value === null || value === '') return [];
  if (Array.isArray(value)) return arrayOf(string)(value, path).map((item) => item.trim()).filter(Boolean);
  if (typeof value === 'string') return value.split(',').map((item) => item.trim()).filter(Boolean);
  throw new DecodeError(path, 'comma-separated string or string array', value);
}

function commandObject(value: unknown, path: string): object {
  const input = inputObject(value, path);
  const [rawOk, okPath] = at(input, 'ok', path);
  const ok = boolean(rawOk, okPath);
  if (!ok) {
    const rawError = field(input, 'error');
    const rawOutput = field(input, 'output');
    const message = typeof rawError === 'string' && rawError.trim()
      ? rawError
      : typeof rawOutput === 'string' && rawOutput.trim() ? rawOutput : '服务端拒绝了该操作';
    const rawCode = field(input, 'code');
    throw new BusinessError(message, value, typeof rawCode === 'string' ? rawCode : undefined);
  }
  return input;
}

export const decodeOkCommand: Decoder<{ ok: true }> = (value, path = '$') => {
  commandObject(value, path);
  return { ok: true };
};

export const decodePaperListItem: Decoder<PaperListItem> = (value, path = '$') => {
  const input = inputObject(value, path);
  const [rawId, idPath] = at(input, 'id', path);
  const id = string(rawId, idPath);
  return {
    id,
    file: string(field(input, 'file'), `${path}.file`),
    title: string(field(input, 'title'), `${path}.title`),
    titleZh: nullableString(field(input, 'title_zh'), `${path}.title_zh`),
    venue: nullableString(field(input, 'venue'), `${path}.venue`),
    year: nullableYear(field(input, 'year'), `${path}.year`),
    type: nullableString(field(input, 'type'), `${path}.type`),
    topic: nullableString(field(input, 'topic'), `${path}.topic`),
    pdfUrl: nullableString(field(input, 'pdf_url'), `${path}.pdf_url`),
    pdfPath: nullableString(field(input, 'pdf_path'), `${path}.pdf_path`),
    url: nullableString(field(input, 'url'), `${path}.url`),
    tldr: nullableString(field(input, 'tldr'), `${path}.tldr`),
    contribution: nullableString(field(input, 'contribution'), `${path}.contribution`),
    citations: nullableInteger(field(input, 'citations'), `${path}.citations`),
    createdAt: nullableString(field(input, 'created_at'), `${path}.created_at`),
    source: nullableString(field(input, 'source'), `${path}.source`),
    arxivId: nullableString(field(input, 'arxiv_id'), `${path}.arxiv_id`),
    doi: nullableString(field(input, 'doi'), `${path}.doi`),
    s2Id: nullableString(field(input, 's2_id'), `${path}.s2_id`),
    openalexId: nullableString(field(input, 'openalex_id'), `${path}.openalex_id`),
    relevance: nullableNumber(field(input, 'relevance'), `${path}.relevance`),
    order: nullableInteger(field(input, 'order'), `${path}.order`),
    ccf: nullableString(field(input, 'ccf'), `${path}.ccf`),
    status: studyStatus(field(input, 'status'), `${path}.status`, true),
    hasNote: boolean(field(input, 'hasNote'), `${path}.hasNote`),
    favorite: boolean(field(input, 'favorite'), `${path}.favorite`),
    hasPdf: boolean(field(input, 'hasPdf'), `${path}.hasPdf`),
  };
};

export const decodePaper = decodePaperListItem;
export const decodePaperList = arrayOf(decodePaperListItem);

const decodePaperRecord: Decoder<PaperRecord> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    id: string(field(input, 'id'), `${path}.id`),
    source: string(field(input, 'source'), `${path}.source`),
    sourceId: nullableString(field(input, 'source_id'), `${path}.source_id`),
    arxivId: nullableString(field(input, 'arxiv_id'), `${path}.arxiv_id`),
    doi: nullableString(field(input, 'doi'), `${path}.doi`),
    s2Id: nullableString(field(input, 's2_id'), `${path}.s2_id`),
    openalexId: nullableString(field(input, 'openalex_id'), `${path}.openalex_id`),
    title: string(field(input, 'title'), `${path}.title`),
    titleZh: nullableString(field(input, 'title_zh'), `${path}.title_zh`),
    titleNorm: nullableString(field(input, 'title_norm'), `${path}.title_norm`),
    authors: stringArray(field(input, 'authors'), `${path}.authors`),
    venue: nullableString(field(input, 'venue'), `${path}.venue`),
    year: nullableYear(field(input, 'year'), `${path}.year`),
    abstract: nullableString(field(input, 'abstract'), `${path}.abstract`),
    tldr: nullableString(field(input, 'tldr'), `${path}.tldr`),
    citations: nullableInteger(field(input, 'citations'), `${path}.citations`),
    s2Fields: stringArray(field(input, 's2_fields'), `${path}.s2_fields`),
    url: nullableString(field(input, 'url'), `${path}.url`),
    pdfUrl: nullableString(field(input, 'pdf_url'), `${path}.pdf_url`),
    pdfPath: nullableString(field(input, 'pdf_path'), `${path}.pdf_path`),
    type: nullableString(field(input, 'type'), `${path}.type`),
    topic: nullableString(field(input, 'topic'), `${path}.topic`),
    task: nullableString(field(input, 'task'), `${path}.task`),
    models: stringArray(field(input, 'models'), `${path}.models`),
    datasets: stringArray(field(input, 'datasets'), `${path}.datasets`),
    contribution: nullableString(field(input, 'contribution'), `${path}.contribution`),
    tags: stringArray(field(input, 'tags'), `${path}.tags`),
    relevance: nullableNumber(field(input, 'relevance'), `${path}.relevance`),
    explainer: nullableString(field(input, 'explainer'), `${path}.explainer`),
    extractedBy: nullableString(field(input, 'extracted_by'), `${path}.extracted_by`),
    orderNo: nullableInteger(field(input, 'order_no'), `${path}.order_no`),
    createdAt: nullableString(field(input, 'created_at'), `${path}.created_at`),
    updatedAt: nullableString(field(input, 'updated_at'), `${path}.updated_at`),
  };
};

export const decodePaperDetail: Decoder<PaperRecord | null> = (value, path = '$') => (
  value === null ? null : decodePaperRecord(value, path)
);

export const decodeReviewPlan: Decoder<ReviewPlan> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    paperId: string(field(input, 'paper_id'), `${path}.paper_id`),
    startedAt: string(field(input, 'started_at'), `${path}.started_at`),
    currentStep: count(field(input, 'current_step'), `${path}.current_step`),
    completedSteps: count(field(input, 'completed_steps'), `${path}.completed_steps`),
    nextDueAt: string(field(input, 'next_due_at'), `${path}.next_due_at`),
    completedAt: nullableString(field(input, 'completed_at'), `${path}.completed_at`),
    updatedAt: string(field(input, 'updated_at'), `${path}.updated_at`),
  };
};

export const decodeReviewItem: Decoder<ReviewItem> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    ...decodeReviewPlan(value, path),
    title: string(field(input, 'title'), `${path}.title`),
    titleZh: nullableString(field(input, 'title_zh'), `${path}.title_zh`),
    venue: nullableString(field(input, 'venue'), `${path}.venue`),
    year: nullableYear(field(input, 'year'), `${path}.year`),
    status: studyStatus(field(input, 'status'), `${path}.status`),
    reviewState: reviewState(field(input, 'review_state'), `${path}.review_state`),
    totalSteps: count(field(input, 'total_steps'), `${path}.total_steps`),
  };
};

const decodeReviewCounts: Decoder<ReviewCounts> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    overdue: count(field(input, 'overdue'), `${path}.overdue`),
    dueToday: count(field(input, 'dueToday'), `${path}.dueToday`),
    upcoming: count(field(input, 'upcoming'), `${path}.upcoming`),
    completed: count(field(input, 'completed'), `${path}.completed`),
  };
};

export const decodeReviewSnapshot: Decoder<ReviewSnapshot> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    today: string(field(input, 'today'), `${path}.today`),
    counts: decodeReviewCounts(field(input, 'counts'), `${path}.counts`),
    overdue: arrayOf(decodeReviewItem)(field(input, 'overdue'), `${path}.overdue`),
    dueToday: arrayOf(decodeReviewItem)(field(input, 'dueToday'), `${path}.dueToday`),
    upcoming: arrayOf(decodeReviewItem)(field(input, 'upcoming'), `${path}.upcoming`),
    completed: arrayOf(decodeReviewItem)(field(input, 'completed'), `${path}.completed`),
  };
};

export const decodeCandidate: Decoder<Candidate> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    source: string(field(input, 'source'), `${path}.source`),
    sourceId: string(field(input, 'source_id'), `${path}.source_id`),
    title: string(field(input, 'title'), `${path}.title`),
    authors: stringArray(field(input, 'authors'), `${path}.authors`),
    venue: nullableString(field(input, 'venue'), `${path}.venue`),
    year: nullableYear(field(input, 'year'), `${path}.year`),
    abstract: nullableString(field(input, 'abstract'), `${path}.abstract`),
    tldr: nullableString(field(input, 'tldr'), `${path}.tldr`),
    fields: stringArray(field(input, 'fields'), `${path}.fields`),
    citations: nullableInteger(field(input, 'citations'), `${path}.citations`),
    url: nullableString(field(input, 'url'), `${path}.url`),
    pdfUrl: nullableString(field(input, 'pdf_url'), `${path}.pdf_url`),
    arxivId: nullableString(field(input, 'arxiv_id'), `${path}.arxiv_id`),
    doi: nullableString(field(input, 'doi'), `${path}.doi`),
    s2Id: nullableString(field(input, 's2_id'), `${path}.s2_id`),
    ccf: nullableString(field(input, 'ccf'), `${path}.ccf`),
    type: nullableString(field(input, 'type'), `${path}.type`),
    topic: nullableString(field(input, 'topic'), `${path}.topic`),
    task: nullableString(field(input, 'task'), `${path}.task`),
    models: stringArray(field(input, 'models'), `${path}.models`),
    datasets: stringArray(field(input, 'datasets'), `${path}.datasets`),
    contribution: nullableString(field(input, 'contribution'), `${path}.contribution`),
    llmTldr: nullableString(field(input, 'llm_tldr'), `${path}.llm_tldr`),
    tags: stringArray(field(input, 'tags'), `${path}.tags`),
    relevance: nullableNumber(field(input, 'relevance'), `${path}.relevance`),
    inLibrary: optionalBoolean(field(input, 'in_library'), `${path}.in_library`),
    candidateId: nullableInteger(field(input, '_cid'), `${path}._cid`),
  };
};

export const decodeVerification: Decoder<Verification> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    venue: nullableString(field(input, 'venue'), `${path}.venue`),
    year: nullableYear(field(input, 'year'), `${path}.year`),
    matched: boolean(field(input, 'matched'), `${path}.matched`),
    skipped: boolean(field(input, 'skipped'), `${path}.skipped`),
    sourceOfTruth: string(field(input, 'source_of_truth'), `${path}.source_of_truth`),
    changed: boolean(field(input, 'changed'), `${path}.changed`),
    originalVenue: nullableString(field(input, 'orig_venue'), `${path}.orig_venue`),
    ccf: nullableString(field(input, 'ccf'), `${path}.ccf`),
    note: string(field(input, 'note'), `${path}.note`),
    error: optionalBoolean(field(input, 'error'), `${path}.error`),
  };
};

function decodeJobBase(input: object, path: string) {
  return {
    id: integer(field(input, 'id'), `${path}.id`),
    query: nullableString(field(input, 'query'), `${path}.query`),
    sources: csv(field(input, 'venues'), `${path}.venues`),
    yearFrom: nullableInteger(field(input, 'year_from'), `${path}.year_from`),
    yearTo: nullableInteger(field(input, 'year_to'), `${path}.year_to`),
    maxPapers: nullableInteger(field(input, 'max_papers'), `${path}.max_papers`),
    minRelevance: nullableNumber(field(input, 'min_relevance'), `${path}.min_relevance`),
    onlyA: optionalBoolean(field(input, 'only_a'), `${path}.only_a`),
    scheduleId: nullableInteger(field(input, 'schedule_id'), `${path}.schedule_id`),
    status: jobStatus(field(input, 'status'), `${path}.status`),
    found: count(field(input, 'found'), `${path}.found`),
    added: count(field(input, 'added'), `${path}.added`),
    skipped: count(field(input, 'skipped'), `${path}.skipped`),
    createdAt: nullableString(field(input, 'created_at'), `${path}.created_at`),
    finishedAt: nullableString(field(input, 'finished_at'), `${path}.finished_at`),
  };
}

export const decodeJobSummary: Decoder<JobSummary> = (value, path = '$') => {
  const input = inputObject(value, path);
  return { ...decodeJobBase(input, path), pending: count(field(input, 'pending'), `${path}.pending`) };
};

export const decodeJobRecord: Decoder<JobRecord> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    ...decodeJobBase(input, path),
    log: nullableString(field(input, 'log'), `${path}.log`),
    queries: stringArray(field(input, 'queries'), `${path}.queries`),
  };
};

export const decodeJobDetail: Decoder<JobDetail> = (value, path = '$') => {
  const input = commandObject(value, path);
  return {
    job: decodeJobRecord(field(input, 'job'), `${path}.job`),
    candidates: arrayOf(decodeCandidate)(field(input, 'candidates'), `${path}.candidates`),
  };
};

export const decodeSchedule: Decoder<Schedule> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    id: integer(field(input, 'id'), `${path}.id`),
    query: string(field(input, 'query'), `${path}.query`),
    sources: csv(field(input, 'sources'), `${path}.sources`),
    years: string(field(input, 'years'), `${path}.years`),
    maxPapers: count(field(input, 'max_papers'), `${path}.max_papers`),
    minRelevance: number(field(input, 'min_relevance'), `${path}.min_relevance`),
    onlyA: boolean(field(input, 'only_a'), `${path}.only_a`),
    everyDays: count(field(input, 'every_days'), `${path}.every_days`),
    enabled: boolean(field(input, 'enabled'), `${path}.enabled`),
    lastRun: nullableString(field(input, 'last_run'), `${path}.last_run`),
    nextRun: nullableString(field(input, 'next_run'), `${path}.next_run`),
    createdAt: nullableString(field(input, 'created_at'), `${path}.created_at`),
  };
};

const decodeCitationNode: Decoder<CitationNode> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    id: string(field(input, 'id'), `${path}.id`),
    title: string(field(input, 'title'), `${path}.title`),
    venue: nullableString(field(input, 'venue'), `${path}.venue`),
    year: nullableYear(field(input, 'year'), `${path}.year`),
    type: nullableString(field(input, 'type'), `${path}.type`),
    topic: nullableString(field(input, 'topic'), `${path}.topic`),
    citations: nullableInteger(field(input, 'citations'), `${path}.citations`),
    indeg: count(field(input, 'indeg'), `${path}.indeg`),
    outdeg: count(field(input, 'outdeg'), `${path}.outdeg`),
  };
};

const decodeCitationLink: Decoder<CitationLink> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    source: string(field(input, 'source'), `${path}.source`),
    target: string(field(input, 'target'), `${path}.target`),
  };
};

export const decodeCitationGraph: Decoder<CitationGraph> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    nodes: arrayOf(decodeCitationNode)(field(input, 'nodes'), `${path}.nodes`),
    links: arrayOf(decodeCitationLink)(field(input, 'links'), `${path}.links`),
    edgeCount: count(field(input, 'edgeCount'), `${path}.edgeCount`),
  };
};

export const decodeSettingsView: Decoder<SettingsView> = (value, path = '$') => {
  const input = inputObject(value, path);
  const text = (key: string) => string(field(input, key), `${path}.${key}`);
  return {
    provider: text('provider'), baseUrl: text('baseUrl'), model: text('model'), apiKeyTail: text('apiKeyTail'),
    hasApiKey: boolean(field(input, 'hasApiKey'), `${path}.hasApiKey`),
    s2KeyTail: text('s2KeyTail'), hasS2Key: boolean(field(input, 'hasS2Key'), `${path}.hasS2Key`),
    pdfDir: text('pdfDir'), explainerDir: text('explainerDir'), translationDir: text('translationDir'),
    defaultPdfDir: text('defaultPdfDir'), defaultExplainerDir: text('defaultExplainerDir'),
    defaultTranslationDir: text('defaultTranslationDir'), resolvedPdfDir: text('resolvedPdfDir'),
    resolvedExplainerDir: text('resolvedExplainerDir'), resolvedTranslationDir: text('resolvedTranslationDir'),
    researchTheme: text('researchTheme'), embedProvider: text('embedProvider'), embedApiBase: text('embedApiBase'),
    embedApiModel: text('embedApiModel'), embedKeyTail: text('embedKeyTail'),
    hasEmbedKey: boolean(field(input, 'hasEmbedKey'), `${path}.hasEmbedKey`),
    obsidianEnabled: optionalStrictBoolean(
      field(input, 'obsidianEnabled'), `${path}.obsidianEnabled`, false,
    ),
    obsidianVaultPath: optionalString(
      field(input, 'obsidianVaultPath'), `${path}.obsidianVaultPath`, '',
    ),
    obsidianRootFolder: optionalString(
      field(input, 'obsidianRootFolder'), `${path}.obsidianRootFolder`, 'Research',
    ),
    obsidianPdfMode: field(input, 'obsidianPdfMode') === undefined
      ? 'none'
      : oneOf(
          ['none', 'reference', 'copy'] as const,
          field(input, 'obsidianPdfMode'),
          `${path}.obsidianPdfMode`,
        ),
    obsidianExportSource: optionalStrictBoolean(
      field(input, 'obsidianExportSource'), `${path}.obsidianExportSource`, true,
    ),
    obsidianExportExplainer: optionalStrictBoolean(
      field(input, 'obsidianExportExplainer'), `${path}.obsidianExportExplainer`, true,
    ),
    obsidianExportTranslation: optionalStrictBoolean(
      field(input, 'obsidianExportTranslation'), `${path}.obsidianExportTranslation`, true,
    ),
    obsidianAutoExport: optionalStrictBoolean(
      field(input, 'obsidianAutoExport'), `${path}.obsidianAutoExport`, false,
    ),
  };
};

const obsidianCountKeys = [
  'exported',
  'unchanged',
  'conflicts',
  'errors',
  'skipped',
  'userManaged',
  'orphaned',
  'deleted',
] as const;

export const decodeObsidianResultCounts: Decoder<ObsidianResultCounts> = (
  value,
  path = '$',
) => {
  const input = inputObject(value, path);
  assertExactKeys(input, obsidianCountKeys, path);
  return {
    exported: count(field(input, 'exported'), `${path}.exported`),
    unchanged: count(field(input, 'unchanged'), `${path}.unchanged`),
    conflicts: count(field(input, 'conflicts'), `${path}.conflicts`),
    errors: count(field(input, 'errors'), `${path}.errors`),
    skipped: count(field(input, 'skipped'), `${path}.skipped`),
    userManaged: count(field(input, 'userManaged'), `${path}.userManaged`),
    orphaned: count(field(input, 'orphaned'), `${path}.orphaned`),
    deleted: count(field(input, 'deleted'), `${path}.deleted`),
  };
};

function decodeObsidianPaperId(
  value: unknown,
  jobType: 'obsidian_export' | 'obsidian_sync',
  path: string,
): string | null {
  if (jobType === 'obsidian_sync') {
    if (value !== null) throw new DecodeError(path, 'null', value);
    return null;
  }
  return string(value, path);
}

const decodeObsidianLastJob: Decoder<ObsidianLastJob> = (value, path = '$') => {
  const input = inputObject(value, path);
  assertExactKeys(input, ['id', 'paperId', 'jobType', 'status'], path);
  const jobType = oneOf(
    ['obsidian_export', 'obsidian_sync'] as const,
    field(input, 'jobType'),
    `${path}.jobType`,
  );
  return {
    id: string(field(input, 'id'), `${path}.id`),
    paperId: decodeObsidianPaperId(field(input, 'paperId'), jobType, `${path}.paperId`),
    jobType,
    status: oneOf(
      ['queued', 'running', 'succeeded', 'failed', 'cancelled'] as const,
      field(input, 'status'),
      `${path}.status`,
    ),
  };
};

export const decodeObsidianStatus: Decoder<ObsidianStatus> = (value, path = '$') => {
  const input = inputObject(value, path);
  assertExactKeys(input, [
    'enabled',
    'vaultConfigured',
    'writable',
    'rootFolder',
    'pdfMode',
    'lastJob',
    'aggregate',
  ], path);
  const rawLastJob = field(input, 'lastJob');
  return {
    enabled: strictBoolean(field(input, 'enabled'), `${path}.enabled`),
    vaultConfigured: strictBoolean(
      field(input, 'vaultConfigured'), `${path}.vaultConfigured`,
    ),
    writable: strictBoolean(field(input, 'writable'), `${path}.writable`),
    rootFolder: string(field(input, 'rootFolder'), `${path}.rootFolder`),
    pdfMode: oneOf(
      ['none', 'reference', 'copy'] as const,
      field(input, 'pdfMode'),
      `${path}.pdfMode`,
    ) as ObsidianPdfMode,
    lastJob: rawLastJob === null
      ? null
      : decodeObsidianLastJob(rawLastJob, `${path}.lastJob`),
    aggregate: decodeObsidianResultCounts(field(input, 'aggregate'), `${path}.aggregate`),
  };
};

export const decodeObsidianTestResult: Decoder<ObsidianTestResult> = (value, path = '$') => {
  const input = inputObject(value, path);
  assertExactKeys(input, ['ok'], path);
  return { ok: strictBoolean(field(input, 'ok'), `${path}.ok`) };
};

function decodeObsidianJobResponse(
  expectedJobType: 'obsidian_export' | 'obsidian_sync',
  value: unknown,
  path = '$',
) {
  const input = inputObject(value, path);
  assertExactKeys(input, ['job', 'deduplicated'], path);
  const jobPath = `${path}.job`;
  const rawJob = inputObject(field(input, 'job'), jobPath);
  assertExactKeys(rawJob, ['id', 'paperId', 'jobType', 'sourceMode', 'status'], jobPath);
  const rawPaperId = field(rawJob, 'paperId');
  const job = decodeProcessingJobSummary({
    id: field(rawJob, 'id'),
    paperId: rawPaperId === null ? '__global__' : rawPaperId,
    jobType: field(rawJob, 'jobType'),
    sourceMode: field(rawJob, 'sourceMode'),
    status: field(rawJob, 'status'),
  }, jobPath);
  if (job.jobType !== expectedJobType) {
    throw new DecodeError(`${jobPath}.jobType`, expectedJobType, job.jobType);
  }
  if (job.sourceMode !== null) {
    throw new DecodeError(`${jobPath}.sourceMode`, 'null', job.sourceMode);
  }
  return {
    job: {
      ...job,
      paperId: decodeObsidianPaperId(rawPaperId, expectedJobType, `${jobPath}.paperId`),
    },
    deduplicated: strictBoolean(
      field(input, 'deduplicated'), `${path}.deduplicated`,
    ),
  };
}

export const decodeObsidianExportJobResponse = (value: unknown, path = '$') => (
  decodeObsidianJobResponse('obsidian_export', value, path)
);

export const decodeObsidianSyncJobResponse = (value: unknown, path = '$') => (
  decodeObsidianJobResponse('obsidian_sync', value, path)
);

export const decodeTitleTranslationStatus: Decoder<TitleTranslationStatus> = (value, path = '$') => {
  const input = commandObject(value, path);
  return {
    pending: count(field(input, 'pending'), `${path}.pending`),
    running: boolean(field(input, 'running'), `${path}.running`),
  };
};

export const decodeExplainerPending: Decoder<ExplainerPending> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    pending: count(field(input, 'pending'), `${path}.pending`),
    withPdf: count(field(input, 'withPdf'), `${path}.withPdf`),
    noPdf: count(field(input, 'noPdf'), `${path}.noPdf`),
  };
};

export const decodePdfStatus: Decoder<PdfStatus> = (value, path = '$') => {
  const input = commandObject(value, path);
  return {
    id: string(field(input, 'id'), `${path}.id`),
    hasPdf: boolean(field(input, 'hasPdf'), `${path}.hasPdf`),
    size: count(field(input, 'size'), `${path}.size`),
    path: string(field(input, 'path'), `${path}.path`),
    canDownload: boolean(field(input, 'canDownload'), `${path}.canDownload`),
  };
};

const decodePdfScanFile: Decoder<PdfScanFile> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    path: string(field(input, 'path'), `${path}.path`),
    name: string(field(input, 'name'), `${path}.name`),
    size: count(field(input, 'size'), `${path}.size`),
  };
};

export const decodePdfScanCommand: Decoder<PdfScan> = (value, path = '$') => {
  const input = commandObject(value, path);
  return {
    dir: string(field(input, 'dir'), `${path}.dir`),
    count: count(field(input, 'count'), `${path}.count`),
    files: arrayOf(decodePdfScanFile)(field(input, 'files'), `${path}.files`),
  };
};

export const decodeTranslateTextCommand: Decoder<{ text: string }> = (value, path = '$') => {
  const input = commandObject(value, path);
  return { text: string(field(input, 'text'), `${path}.text`) };
};

export const decodeLlmTestCommand: Decoder<{ output: string }> = (value, path = '$') => {
  const input = commandObject(value, path);
  return { output: string(field(input, 'output'), `${path}.output`) };
};

export const decodeExpandCommand: Decoder<{ queries: string[] }> = (value, path = '$') => {
  const input = commandObject(value, path);
  return { queries: arrayOf(string)(field(input, 'queries'), `${path}.queries`) };
};

export const decodeSemanticHit: Decoder<SemanticHit> = (value, path = '$') => {
  const input = inputObject(value, path);
  return {
    id: string(field(input, 'id'), `${path}.id`),
    score: number(field(input, 'score'), `${path}.score`),
  };
};

export const decodeJobList = arrayOf(decodeJobSummary);
export const decodeScheduleList = arrayOf(decodeSchedule);

export function decodeCommandId(value: unknown, path = '$'): string | number {
  const input = commandObject(value, path);
  const rawId = field(input, 'id');
  if (typeof rawId === 'string') return rawId;
  return integer(rawId, `${path}.id`);
}

export function decodeCommandChanges(value: unknown, path = '$'): number {
  const input = commandObject(value, path);
  return count(field(input, 'changes'), `${path}.changes`);
}

export const decodeReviewStartCommand: Decoder<{ plan: ReviewPlan }> = (value, path = '$') => {
  const input = commandObject(value, path);
  return { plan: decodeReviewPlan(field(input, 'plan'), `${path}.plan`) };
};

export const decodeReviewCompleteCommand: Decoder<{ plan: ReviewPlan; reviews: ReviewSnapshot }> = (value, path = '$') => {
  const input = commandObject(value, path);
  return {
    plan: decodeReviewPlan(field(input, 'plan'), `${path}.plan`),
    reviews: decodeReviewSnapshot(field(input, 'reviews'), `${path}.reviews`),
  };
};

export const decodeReviewSnapshotCommand: Decoder<ReviewSnapshot> = (value, path = '$') => {
  commandObject(value, path);
  return decodeReviewSnapshot(value, path);
};

export const decodeOutputCommand: Decoder<{ output: string; code: number | null }> = (value, path = '$') => {
  const input = commandObject(value, path);
  return {
    output: string(field(input, 'output'), `${path}.output`),
    code: nullableInteger(field(input, 'code'), `${path}.code`),
  };
};
