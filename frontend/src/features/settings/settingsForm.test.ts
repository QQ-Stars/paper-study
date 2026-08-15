import { describe, expect, it } from 'vitest';
import type { SettingsView } from '../../lib/api/types';

import {
  buildSettingsUpdate,
  createSettingsDraft,
  emptySecrets,
  secretPlaceholder,
} from './settingsForm';

const view: SettingsView = {
  provider: 'openai',
  baseUrl: 'https://api.example.test/v1',
  model: 'research-model',
  apiKeyTail: '1234',
  hasApiKey: true,
  s2KeyTail: '5678',
  hasS2Key: true,
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
  embedKeyTail: '9012',
  hasEmbedKey: true,
};

describe('settings payload', () => {
  it('never copies masks or blank secrets into the update', () => {
    const update = buildSettingsUpdate(createSettingsDraft(view), emptySecrets());

    expect(update).not.toHaveProperty('apiKey');
    expect(update).not.toHaveProperty('s2ApiKey');
    expect(update).not.toHaveProperty('embedApiKey');
    expect(JSON.stringify(update)).not.toContain('1234');
    expect(JSON.stringify(update)).not.toContain('5678');
    expect(JSON.stringify(update)).not.toContain('9012');
  });

  it('includes every editable directory, research, and embedding field', () => {
    const update = buildSettingsUpdate(createSettingsDraft(view), {
      apiKey: ' new-primary ',
      s2ApiKey: ' new-s2 ',
      embedApiKey: ' new-embed ',
    });

    expect(update).toEqual({
      provider: 'openai',
      baseUrl: 'https://api.example.test/v1',
      model: 'research-model',
      apiKey: 'new-primary',
      s2ApiKey: 'new-s2',
      pdfDir: 'papers',
      explainerDir: 'explainers',
      translationDir: 'translations',
      researchTheme: 'interfaces',
      embedProvider: 'openai',
      embedApiBase: 'https://embed.example.test/v1',
      embedApiModel: 'embed-model',
      embedApiKey: 'new-embed',
      obsidianEnabled: false,
      obsidianVaultPath: '',
      obsidianRootFolder: 'Research',
      obsidianPdfMode: 'none',
      obsidianExportSource: true,
      obsidianExportExplainer: true,
      obsidianExportTranslation: true,
      obsidianAutoExport: false,
    });
  });

  it('describes configured secrets without using the mask as a value', () => {
    expect(secretPlaceholder(true, '1234')).toContain('尾号 1234');
    expect(secretPlaceholder(true, '1234')).toContain('留空则保留');
    expect(secretPlaceholder(false, '')).toBe('尚未配置');
  });
});
