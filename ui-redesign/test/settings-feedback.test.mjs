import assert from 'node:assert/strict';
import test from 'node:test';

import { formatLlmConnectionResult } from '../src/components/settingsFeedback.ts';

test('LLM connection feedback preserves the backend-safe failure detail', () => {
  assert.equal(
    formatLlmConnectionResult({ ok: false, output: 'Credential is not configured.' }),
    '失败：Credential is not configured.',
  );
});

test('LLM connection feedback supports the compatibility error field and success state', () => {
  assert.equal(
    formatLlmConnectionResult({ ok: false, error: '模型不存在' }),
    '失败：模型不存在',
  );
  assert.equal(formatLlmConnectionResult({ ok: true, output: 'ignored' }), '连通正常');
});
