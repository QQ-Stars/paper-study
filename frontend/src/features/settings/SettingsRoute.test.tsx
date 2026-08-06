import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { resetWorkspaceStore } from '../../app/stores/workspaceStore';
import type { SettingsView } from '../../lib/api/types';
import { Component } from './SettingsRoute';

const apiMocks = vi.hoisted(() => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  testLlm: vi.fn(),
}));

vi.mock('../../lib/api/workspaceApi', () => ({
  workspaceApi: apiMocks,
}));

const settings: SettingsView = {
  provider: 'openai',
  baseUrl: 'https://api.example.test/v1',
  model: 'research-model',
  apiKeyTail: '1234',
  hasApiKey: true,
  s2KeyTail: '',
  hasS2Key: false,
  pdfDir: 'papers',
  explainerDir: 'explainers',
  translationDir: 'translations',
  defaultPdfDir: 'default-papers',
  defaultExplainerDir: 'default-explainers',
  defaultTranslationDir: 'default-translations',
  resolvedPdfDir: 'C:/study/papers',
  resolvedExplainerDir: 'C:/study/explainers',
  resolvedTranslationDir: 'C:/study/translations',
  researchTheme: 'interfaces',
  embedProvider: 'openai',
  embedApiBase: 'https://embed.example.test/v1',
  embedApiModel: 'embed-model',
  embedKeyTail: '9876',
  hasEmbedKey: true,
};

function renderSettings() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <Component />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  resetWorkspaceStore();
  apiMocks.getSettings.mockReset().mockResolvedValue(settings);
  apiMocks.saveSettings.mockReset().mockResolvedValue(undefined);
  apiMocks.testLlm.mockReset().mockResolvedValue({ output: '连接正常' });
});

describe('Settings route', () => {
  it('keeps secrets blank and submits only typed server settings', async () => {
    const user = userEvent.setup();
    renderSettings();

    const apiKey = await screen.findByLabelText('API Key');
    expect(apiKey).toHaveValue('');
    expect(apiKey).toHaveAttribute('placeholder', expect.stringContaining('尾号 1234'));

    await user.clear(screen.getByLabelText('PDF 目录'));
    await user.type(screen.getByLabelText('PDF 目录'), 'new-papers');
    await user.type(apiKey, 'new-secret');
    await user.click(screen.getByRole('button', { name: '舒适' }));
    await user.click(screen.getByRole('button', { name: '保存设置' }));

    await waitFor(() => expect(apiMocks.saveSettings).toHaveBeenCalledOnce());
    expect(apiMocks.saveSettings).toHaveBeenCalledWith(expect.objectContaining({
      apiKey: 'new-secret',
      pdfDir: 'new-papers',
      explainerDir: 'explainers',
      translationDir: 'translations',
      researchTheme: 'interfaces',
      embedProvider: 'openai',
      embedApiBase: 'https://embed.example.test/v1',
      embedApiModel: 'embed-model',
    }));
    expect(apiMocks.saveSettings.mock.calls[0][0]).not.toHaveProperty('density');
    expect(apiMocks.saveSettings.mock.calls[0][0]).not.toHaveProperty('theme');
    expect(apiMocks.saveSettings.mock.calls[0][0]).not.toHaveProperty('embedApiKey');
  });

  it('keeps model-test and save failures in independent status regions', async () => {
    const user = userEvent.setup();
    apiMocks.testLlm.mockRejectedValue(new Error('模型连接失败'));
    renderSettings();

    await user.click(await screen.findByRole('button', { name: '测试模型连接' }));
    expect(await screen.findByText('模型连接失败')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '保存设置' }));
    await waitFor(() => expect(apiMocks.saveSettings).toHaveBeenCalledOnce());
    expect(screen.getByText('设置已由服务器确认。')).toBeInTheDocument();
    expect(screen.getByText('模型连接失败')).toBeInTheDocument();
  });
});
