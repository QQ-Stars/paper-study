import type { SettingsUpdate, SettingsView } from '../../lib/api/types';

export interface SettingsDraft {
  provider: string;
  baseUrl: string;
  model: string;
  pdfDir: string;
  explainerDir: string;
  translationDir: string;
  researchTheme: string;
  embedProvider: string;
  embedApiBase: string;
  embedApiModel: string;
  pdfTextProvider: 'default' | 'ocr';
  ocrBaseUrl: string;
  ocrModel: string;
  obsidianEnabled: boolean;
  obsidianVaultPath: string;
  obsidianRootFolder: string;
  obsidianPdfMode: 'none' | 'reference' | 'copy';
  obsidianExportSource: boolean;
  obsidianExportExplainer: boolean;
  obsidianExportTranslation: boolean;
  obsidianAutoExport: boolean;
}

export interface SecretDraft {
  apiKey: string;
  s2ApiKey: string;
  embedApiKey: string;
  ocrApiKey: string;
}

export function createSettingsDraft(view: SettingsView): SettingsDraft {
  return {
    provider: view.provider,
    baseUrl: view.baseUrl,
    model: view.model,
    pdfDir: view.pdfDir,
    explainerDir: view.explainerDir,
    translationDir: view.translationDir,
    researchTheme: view.researchTheme,
    embedProvider: view.embedProvider,
    embedApiBase: view.embedApiBase,
    embedApiModel: view.embedApiModel,
    pdfTextProvider: view.pdfTextProvider === 'ocr' ? 'ocr' : 'default',
    ocrBaseUrl: view.ocrBaseUrl,
    ocrModel: view.ocrModel,
    obsidianEnabled: view.obsidianEnabled ?? false,
    obsidianVaultPath: view.obsidianVaultPath ?? '',
    obsidianRootFolder: view.obsidianRootFolder ?? 'Research',
    obsidianPdfMode: view.obsidianPdfMode ?? 'none',
    obsidianExportSource: view.obsidianExportSource ?? true,
    obsidianExportExplainer: view.obsidianExportExplainer ?? true,
    obsidianExportTranslation: view.obsidianExportTranslation ?? true,
    obsidianAutoExport: view.obsidianAutoExport ?? false,
  };
}

export function emptySecrets(): SecretDraft {
  return { apiKey: '', s2ApiKey: '', embedApiKey: '', ocrApiKey: '' };
}

export function buildSettingsUpdate(
  draft: SettingsDraft,
  secrets: SecretDraft,
): SettingsUpdate {
  const update: SettingsUpdate = {
    provider: draft.provider,
    baseUrl: draft.baseUrl,
    model: draft.model,
    pdfDir: draft.pdfDir,
    explainerDir: draft.explainerDir,
    translationDir: draft.translationDir,
    researchTheme: draft.researchTheme,
    embedProvider: draft.embedProvider,
    embedApiBase: draft.embedApiBase,
    embedApiModel: draft.embedApiModel,
    pdfTextProvider: draft.pdfTextProvider,
    ocrBaseUrl: draft.ocrBaseUrl,
    ocrModel: draft.ocrModel,
    obsidianEnabled: draft.obsidianEnabled,
    obsidianVaultPath: draft.obsidianVaultPath,
    obsidianRootFolder: draft.obsidianRootFolder,
    obsidianPdfMode: draft.obsidianPdfMode,
    obsidianExportSource: draft.obsidianExportSource,
    obsidianExportExplainer: draft.obsidianExportExplainer,
    obsidianExportTranslation: draft.obsidianExportTranslation,
    obsidianAutoExport: draft.obsidianAutoExport,
  };
  if (secrets.apiKey.trim()) update.apiKey = secrets.apiKey.trim();
  if (secrets.s2ApiKey.trim()) update.s2ApiKey = secrets.s2ApiKey.trim();
  if (secrets.embedApiKey.trim()) {
    update.embedApiKey = secrets.embedApiKey.trim();
  }
  if (secrets.ocrApiKey.trim()) {
    update.ocrApiKey = secrets.ocrApiKey.trim();
  }
  return update;
}

export function secretPlaceholder(
  configured: boolean,
  tail: string,
): string {
  return configured
    ? `已配置${tail ? ` · 尾号 ${tail}` : ''}，留空则保留`
    : '尚未配置';
}
