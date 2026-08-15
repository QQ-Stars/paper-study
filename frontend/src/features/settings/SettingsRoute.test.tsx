import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { SettingsView } from '../../lib/api/types';
import { resetWorkspaceStore } from '../../lib/workspace';
import {
  obsidianJobResponseFixture,
  obsidianStatusFixture,
  obsidianTestFixture,
} from '../../test/fixtures/obsidian';
import { Component } from './SettingsRoute';

const apiMocks = vi.hoisted(() => ({
  getSettings: vi.fn(),
  saveSettings: vi.fn(),
  testLlm: vi.fn(),
}));

const obsidianMocks = vi.hoisted(() => ({
  getStatus: vi.fn(),
  testAccess: vi.fn(),
  exportPaper: vi.fn(),
  sync: vi.fn(),
  waitForTerminal: vi.fn(),
}));

vi.mock('../../lib/api/settingsGateway', () => ({
  settingsGateway: apiMocks,
}));

vi.mock('../../lib/api/obsidianGateway', () => ({
  obsidianGateway: {
    getStatus: obsidianMocks.getStatus,
    testAccess: obsidianMocks.testAccess,
    exportPaper: obsidianMocks.exportPaper,
    sync: obsidianMocks.sync,
  },
}));

vi.mock('../../lib/api/processingGateway', () => ({
  processingGateway: { waitForTerminal: obsidianMocks.waitForTerminal },
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
  obsidianEnabled: true,
  obsidianVaultPath: 'X:/fixture-vault',
  obsidianRootFolder: 'Research',
  obsidianPdfMode: 'reference',
  obsidianExportSource: true,
  obsidianExportExplainer: true,
  obsidianExportTranslation: false,
  obsidianAutoExport: false,
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((accept) => {
    resolve = accept;
  });
  return { promise, resolve };
}

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
  obsidianMocks.getStatus.mockReset().mockResolvedValue(obsidianStatusFixture);
  obsidianMocks.testAccess.mockReset().mockResolvedValue(obsidianTestFixture);
  obsidianMocks.exportPaper.mockReset().mockResolvedValue(obsidianJobResponseFixture);
  obsidianMocks.sync.mockReset().mockResolvedValue({
    ...obsidianJobResponseFixture,
    job: {
      ...obsidianJobResponseFixture.job,
      id: 'job-obsidian-sync',
      paperId: null,
      jobType: 'obsidian_sync',
    },
  });
  obsidianMocks.waitForTerminal.mockReset().mockResolvedValue({ status: 'succeeded' });
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

  it('shows and saves all eight non-secret Obsidian settings', async () => {
    const user = userEvent.setup();
    renderSettings();

    const enabled = await screen.findByLabelText('启用 Obsidian 投影');
    expect(enabled).toBeChecked();
    expect(screen.getByLabelText('Vault 路径')).toHaveValue('X:/fixture-vault');
    expect(screen.getByLabelText('受管根目录')).toHaveValue('Research');
    expect(screen.getByLabelText('PDF 投影模式')).toHaveValue('reference');
    expect(screen.getByLabelText('导出 SourceDocument')).toBeChecked();
    expect(screen.getByLabelText('导出讲解')).toBeChecked();
    expect(screen.getByLabelText('导出翻译')).not.toBeChecked();
    expect(screen.getByLabelText('自动导出')).not.toBeChecked();

    await user.clear(screen.getByLabelText('Vault 路径'));
    await user.type(screen.getByLabelText('Vault 路径'), 'Y:/fixture-vault-next');
    await user.clear(screen.getByLabelText('受管根目录'));
    await user.type(screen.getByLabelText('受管根目录'), 'Research/Team');
    await user.selectOptions(screen.getByLabelText('PDF 投影模式'), 'copy');
    await user.click(screen.getByLabelText('导出 SourceDocument'));
    await user.click(screen.getByLabelText('导出翻译'));
    await user.click(screen.getByLabelText('自动导出'));
    await user.click(screen.getByRole('button', { name: '保存设置' }));

    await waitFor(() => expect(apiMocks.saveSettings).toHaveBeenCalledOnce());
    expect(apiMocks.saveSettings).toHaveBeenCalledWith(expect.objectContaining({
      obsidianEnabled: true,
      obsidianVaultPath: 'Y:/fixture-vault-next',
      obsidianRootFolder: 'Research/Team',
      obsidianPdfMode: 'copy',
      obsidianExportSource: false,
      obsidianExportExplainer: true,
      obsidianExportTranslation: true,
      obsidianAutoExport: true,
    }));
  });

  it('uses recoverable pending and error states for Obsidian test and sync', async () => {
    const user = userEvent.setup();
    const probe = deferred<typeof obsidianTestFixture>();
    obsidianMocks.testAccess.mockReturnValueOnce(probe.promise);
    obsidianMocks.sync.mockRejectedValueOnce(new Error('Obsidian 同步失败'));
    renderSettings();

    const testButton = await screen.findByRole('button', { name: '测试 Obsidian 写权限' });
    await user.click(testButton);
    expect(screen.getByRole('button', { name: '正在测试 Obsidian…' })).toBeDisabled();
    probe.resolve(obsidianTestFixture);
    expect(await screen.findByText('Obsidian 写权限可用。')).toBeInTheDocument();

    const syncButton = screen.getByRole('button', { name: '同步到 Obsidian' });
    await user.click(syncButton);
    expect(await screen.findByText('Obsidian 同步失败')).toBeInTheDocument();
    expect(syncButton).toBeEnabled();

    await user.click(syncButton);
    await waitFor(() => expect(obsidianMocks.sync).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(obsidianMocks.waitForTerminal).toHaveBeenCalledOnce());
    expect(await screen.findByText('Obsidian 同步已完成。')).toBeInTheDocument();
  });
});
