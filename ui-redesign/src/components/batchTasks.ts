import {
  buildBatchLimitRequest,
  buildDownloadBatchRequest,
  type BatchRequestResult,
} from './batchLimits.ts';

export interface BatchLimitDraft {
  value: string;
  inputInvalid: boolean;
}

type BatchRequest = { limit: number } | { ids: string[]; limit: number };
type BatchRequestBuilder = (
  value: string,
  inputInvalid: boolean,
  ids: readonly string[],
) => BatchRequestResult<BatchRequest>;

interface BatchTaskDefinition {
  defaultValue: string;
  inputId: string;
  accessibleName: string;
  buildRequest: BatchRequestBuilder;
}

export const BATCH_TASKS = {
  titleTranslations: {
    defaultValue: '10',
    inputId: 'title-translation-limit',
    accessibleName: '本次处理篇数：标题翻译',
    buildRequest: (value: string, inputInvalid: boolean) =>
      buildBatchLimitRequest(value, inputInvalid),
  },
  pdfDownloads: {
    defaultValue: '20',
    inputId: 'pdf-download-limit',
    accessibleName: '本次处理篇数：PDF 补下载',
    buildRequest: (value: string, inputInvalid: boolean, ids: readonly string[]) =>
      buildDownloadBatchRequest(value, ids, inputInvalid),
  },
  explanations: {
    defaultValue: '3',
    inputId: 'explain-batch-limit',
    accessibleName: '本次处理篇数：批量讲解',
    buildRequest: (value: string, inputInvalid: boolean) =>
      buildBatchLimitRequest(value, inputInvalid),
  },
  ocrMarkdown: {
    defaultValue: '3',
    inputId: 'ocr-batch-limit',
    accessibleName: '本次处理篇数：PDF 转 Markdown',
    buildRequest: (value: string, inputInvalid: boolean) =>
      buildBatchLimitRequest(value, inputInvalid),
  },
  metadataEnrichment: {
    defaultValue: '10',
    inputId: 'enrich-limit',
    accessibleName: '本次处理篇数：元数据补全',
    buildRequest: (value: string, inputInvalid: boolean) =>
      buildBatchLimitRequest(value, inputInvalid),
  },
} as const satisfies Record<string, BatchTaskDefinition>;

export type BatchTaskKey = keyof typeof BATCH_TASKS;

export function createBatchLimitDraft(task: BatchTaskKey): BatchLimitDraft {
  return { value: BATCH_TASKS[task].defaultValue, inputInvalid: false };
}

export function createBatchLimitDrafts(): Record<BatchTaskKey, BatchLimitDraft> {
  return Object.fromEntries(
    (Object.keys(BATCH_TASKS) as BatchTaskKey[]).map((task) => [task, createBatchLimitDraft(task)]),
  ) as Record<BatchTaskKey, BatchLimitDraft>;
}

export function buildBatchTaskRequest(
  task: BatchTaskKey,
  value: string,
  inputInvalid = false,
  ids: readonly string[] = [],
): BatchRequestResult<BatchRequest> {
  return BATCH_TASKS[task].buildRequest(value, inputInvalid, ids);
}
