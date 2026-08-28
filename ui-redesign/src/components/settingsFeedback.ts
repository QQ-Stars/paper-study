export interface LlmConnectionResult {
  ok: boolean;
  output?: string;
  error?: string;
}

export interface CredentialPresentationInput {
  hasKey: boolean;
  keyTail: string;
  environmentManaged: boolean;
}

export function buildLlmConnectionPayload(
  draft: Record<string, unknown>,
  apiKey: string,
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const field of ['provider', 'baseUrl', 'model', 'llmTimeout']) {
    if (draft[field] !== undefined) payload[field] = draft[field];
  }
  const submittedKey = apiKey.trim();
  if (submittedKey) payload.apiKey = submittedKey;
  return payload;
}

export function credentialPresentation({
  hasKey,
  keyTail,
  environmentManaged,
}: CredentialPresentationInput): {
  label: string;
  tone: 'amber' | 'jade' | 'venue';
  canClear: boolean;
} {
  if (!hasKey) return { label: '未配置', tone: 'amber', canClear: false };
  if (environmentManaged) {
    return {
      label: `环境变量${keyTail ? ` ${keyTail}` : ''}`,
      tone: 'venue',
      canClear: false,
    };
  }
  return {
    label: `已保存${keyTail ? ` ${keyTail}` : ''}`,
    tone: 'jade',
    canClear: true,
  };
}

export function formatLlmConnectionResult(result: LlmConnectionResult): string {
  if (result.ok) return '连通正常';
  const detail = [result.output, result.error].find(
    (value): value is string => typeof value === 'string' && value.trim().length > 0,
  );
  return `失败：${detail?.trim() || '检查 Key / Base URL / 模型'}`;
}
