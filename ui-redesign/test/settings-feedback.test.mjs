import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildLlmConnectionPayload,
  credentialPresentation,
  formatLlmConnectionResult,
} from '../src/components/settingsFeedback.ts';

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

test('LLM connection test sends the current unsaved form values', () => {
  assert.deepEqual(
    buildLlmConnectionPayload(
      {
        provider: 'other',
        baseUrl: 'https://form.example/v1',
        model: 'form-model',
        llmTimeout: 4200,
      },
      '  form-secret  ',
    ),
    {
      provider: 'other',
      baseUrl: 'https://form.example/v1',
      model: 'form-model',
      llmTimeout: 4200,
      apiKey: 'form-secret',
    },
  );

  assert.deepEqual(
    buildLlmConnectionPayload(
      {
        provider: 'openai',
        baseUrl: 'https://saved-key.example/v1',
        model: 'saved-key-model',
        llmTimeout: 0,
      },
      '   ',
    ),
    {
      provider: 'openai',
      baseUrl: 'https://saved-key.example/v1',
      model: 'saved-key-model',
      llmTimeout: 0,
    },
  );
});

test('environment-managed credentials are identified and cannot be cleared in settings', () => {
  assert.deepEqual(
    credentialPresentation({
      hasKey: true,
      keyTail: '****1234',
      environmentManaged: true,
    }),
    {
      label: '环境变量 ****1234',
      tone: 'venue',
      canClear: false,
    },
  );
  assert.deepEqual(
    credentialPresentation({
      hasKey: true,
      keyTail: '****5678',
      environmentManaged: false,
    }),
    {
      label: '已保存 ****5678',
      tone: 'jade',
      canClear: true,
    },
  );
});
