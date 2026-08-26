export interface LlmConnectionResult {
  ok: boolean;
  output?: string;
  error?: string;
}

export function formatLlmConnectionResult(result: LlmConnectionResult): string {
  if (result.ok) return '连通正常';
  const detail = [result.output, result.error].find(
    (value): value is string => typeof value === 'string' && value.trim().length > 0,
  );
  return `失败：${detail?.trim() || '检查 Key / Base URL / 模型'}`;
}
