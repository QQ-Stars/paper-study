export type BatchLimitResult =
  | { valid: true; limit: number }
  | { valid: false; error: string };

export type BatchRequestResult<T> =
  | { valid: true; request: T }
  | { valid: false; error: string };

export function isBatchLimitText(value: string): boolean {
  return value === '' || /^\d+$/.test(value);
}

export function batchLimitInsertionText(
  syntheticData: unknown,
  nativeData: unknown,
): string | null {
  if (typeof syntheticData === 'string') return syntheticData;
  return typeof nativeData === 'string' ? nativeData : null;
}

export function parseBatchLimit(value: string, inputInvalid = false): BatchLimitResult {
  if (inputInvalid) return { valid: false, error: '请输入非负整数' };
  if (value === '') return { valid: true, limit: 0 };
  if (!isBatchLimitText(value)) return { valid: false, error: '请输入非负整数' };

  const limit = Number(value);
  if (!Number.isSafeInteger(limit)) {
    return { valid: false, error: '数值超出安全整数范围' };
  }
  return { valid: true, limit };
}

export function batchLimitLabel(value: string, inputInvalid = false): string {
  const result = parseBatchLimit(value, inputInvalid);
  if (!result.valid) return '输入无效';
  return result.limit === 0 ? '全部' : `${result.limit} 篇`;
}

export function buildBatchLimitRequest(
  value: string,
  inputInvalid = false,
): BatchRequestResult<{ limit: number }> {
  const result = parseBatchLimit(value, inputInvalid);
  if (!result.valid) return result;
  return { valid: true, request: { limit: result.limit } };
}

export function buildDownloadBatchRequest(
  value: string,
  ids: readonly string[],
  inputInvalid = false,
): BatchRequestResult<{ ids: string[]; limit: number }> {
  const result = parseBatchLimit(value, inputInvalid);
  if (!result.valid) return result;
  return { valid: true, request: { ids: [...ids], limit: result.limit } };
}
