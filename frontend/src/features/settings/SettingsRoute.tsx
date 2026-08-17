/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import { useState, type FormEvent, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { settingsKeys } from '../../lib/api/keys';
import type { SettingsUpdate, SettingsView } from '../../lib/api/types';
import { artifactGateway } from '../../lib/api/artifactGateway';
import { insightsGateway } from '../../lib/api/insightsGateway';
import { settingsGateway } from '../../lib/api/settingsGateway';
import { useObsidianProjection } from '../../lib/api/useObsidianProjection';
import {
  type WorkspaceRouteHandle,
  useWorkspaceStore,
} from '../../lib/workspace';
import {
  buildSettingsUpdate,
  createSettingsDraft,
  emptySecrets,
  secretPlaceholder,
  type SecretDraft,
  type SettingsDraft,
} from './settingsForm';
import './settings.css';

export const handle = {
  title: '设置',
  layout: 'standard',
} satisfies WorkspaceRouteHandle;

export const ErrorBoundary = RouteErrorBoundary;

function message(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : '操作失败，请检查服务端配置。';
}

function Field({
  label,
  hint,
  children,
}: {
  readonly label: string;
  readonly hint?: string;
  readonly children: ReactNode;
}) {
  return (
    <div className="settings-field">
      <label>
        <span>{label}</span>
        {children}
      </label>
      {hint ? <small>{hint}</small> : null}
    </div>
  );
}

function SettingsForm({ settings }: { readonly settings: SettingsView }) {
  const queryClient = useQueryClient();
  const density = useWorkspaceStore((state) => state.density);
  const setDensity = useWorkspaceStore((state) => state.setDensity);
  const [draft, setDraft] = useState<SettingsDraft>(() => createSettingsDraft(settings));
  const [secrets, setSecrets] = useState<SecretDraft>(emptySecrets);
  const save = useMutation({
    mutationFn: (update: SettingsUpdate) => settingsGateway.saveSettings(update),
    onSuccess: async () => {
      setSecrets(emptySecrets());
      await queryClient.invalidateQueries({ queryKey: settingsKeys.view() });
    },
  });
  const test = useMutation({
    mutationFn: () => settingsGateway.testLlm(),
  });
  // 语义索引维护（对齐旧版 reindexBtn）：missing=只补缺失，all=全库重建。
  const embedIndex = useMutation({
    mutationFn: (scope: 'all' | 'missing') => insightsGateway.embed(scope),
  });
  // 中文题名批量生成（对齐旧版 titleZhBatchBtn）。
  const translateTitles = useMutation({
    mutationFn: () => artifactGateway.translateTitles(0),
  });
  const titleStatus = useQuery({
    queryKey: ['title-translations', 'status'],
    queryFn: ({ signal }) => artifactGateway.getTitleTranslationStatus(signal),
  });
  const obsidian = useObsidianProjection();

  const updateDraft = <K extends keyof SettingsDraft>(
    field: K,
    value: SettingsDraft[K],
  ) => {
    setDraft((current) => ({ ...current, [field]: value }));
  };
  const updateSecret = (field: keyof SecretDraft, value: string) => {
    setSecrets((current) => ({ ...current, [field]: value }));
  };
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    save.mutate(buildSettingsUpdate(draft, secrets));
  };

  return (
    <form className="settings-route__form" onSubmit={submit}>
      <section className="settings-section" aria-labelledby="settings-model-heading">
        <header>
          <p>GENERATION</p>
          <h2 id="settings-model-heading">生成模型</h2>
          <span>密钥始终保持空白；留空保存不会覆盖现有值。</span>
        </header>
        <div className="settings-grid">
          <Field label="Provider">
            <input value={draft.provider} onChange={(event) => updateDraft('provider', event.currentTarget.value)} />
          </Field>
          <Field label="Base URL">
            <input type="url" value={draft.baseUrl} onChange={(event) => updateDraft('baseUrl', event.currentTarget.value)} />
          </Field>
          <Field label="Model">
            <input value={draft.model} onChange={(event) => updateDraft('model', event.currentTarget.value)} />
          </Field>
          <Field label="API Key" hint={secretPlaceholder(settings.hasApiKey, settings.apiKeyTail)}>
            <input
              type="password"
              autoComplete="new-password"
              aria-label="API Key"
              value={secrets.apiKey}
              placeholder={secretPlaceholder(settings.hasApiKey, settings.apiKeyTail)}
              onChange={(event) => updateSecret('apiKey', event.currentTarget.value)}
            />
          </Field>
          <Field label="Semantic Scholar Key" hint={secretPlaceholder(settings.hasS2Key, settings.s2KeyTail)}>
            <input
              type="password"
              autoComplete="new-password"
              value={secrets.s2ApiKey}
              placeholder={secretPlaceholder(settings.hasS2Key, settings.s2KeyTail)}
              onChange={(event) => updateSecret('s2ApiKey', event.currentTarget.value)}
            />
          </Field>
        </div>
        <div className="settings-actions">
          <button type="button" disabled={test.isPending} onClick={() => test.mutate()}>
            {test.isPending ? '正在测试…' : '测试模型连接'}
          </button>
          {test.isSuccess ? <output className="settings-status settings-status--ok">{test.data.output}</output> : null}
          {test.isError ? <output className="settings-status settings-status--error">{message(test.error)}</output> : null}
        </div>
      </section>

      <section className="settings-section" aria-labelledby="settings-storage-heading">
        <header>
          <p>STORAGE</p>
          <h2 id="settings-storage-heading">研究文件</h2>
          <span>相对路径由服务端解析，下面同时显示当前解析结果。</span>
        </header>
        <div className="settings-grid">
          <Field label="PDF 目录" hint={`当前解析：${settings.resolvedPdfDir || settings.defaultPdfDir}`}>
            <input value={draft.pdfDir} onChange={(event) => updateDraft('pdfDir', event.currentTarget.value)} />
          </Field>
          <Field label="讲解目录" hint={`当前解析：${settings.resolvedExplainerDir || settings.defaultExplainerDir}`}>
            <input value={draft.explainerDir} onChange={(event) => updateDraft('explainerDir', event.currentTarget.value)} />
          </Field>
          <Field label="翻译目录" hint={`当前解析：${settings.resolvedTranslationDir || settings.defaultTranslationDir}`}>
            <input value={draft.translationDir} onChange={(event) => updateDraft('translationDir', event.currentTarget.value)} />
          </Field>
          <Field label="研究主题">
            <input value={draft.researchTheme} onChange={(event) => updateDraft('researchTheme', event.currentTarget.value)} />
          </Field>
        </div>
      </section>

      <section className="settings-section" aria-labelledby="settings-embed-heading">
        <header>
          <p>EMBEDDINGS</p>
          <h2 id="settings-embed-heading">语义索引</h2>
        </header>
        <div className="settings-grid">
          <Field label="Embedding Provider">
            <input value={draft.embedProvider} onChange={(event) => updateDraft('embedProvider', event.currentTarget.value)} />
          </Field>
          <Field label="Embedding Base URL">
            <input type="url" value={draft.embedApiBase} onChange={(event) => updateDraft('embedApiBase', event.currentTarget.value)} />
          </Field>
          <Field label="Embedding Model">
            <input value={draft.embedApiModel} onChange={(event) => updateDraft('embedApiModel', event.currentTarget.value)} />
          </Field>
          <Field label="Embedding API Key" hint={secretPlaceholder(settings.hasEmbedKey, settings.embedKeyTail)}>
            <input
              type="password"
              autoComplete="new-password"
              value={secrets.embedApiKey}
              placeholder={secretPlaceholder(settings.hasEmbedKey, settings.embedKeyTail)}
              onChange={(event) => updateSecret('embedApiKey', event.currentTarget.value)}
            />
          </Field>
        </div>
        <div className="settings-actions">
          <button
            type="button"
            disabled={embedIndex.isPending}
            onClick={() => embedIndex.mutate('missing')}
          >
            {embedIndex.isPending && embedIndex.variables === 'missing' ? '索引中…' : '补齐缺失索引'}
          </button>
          <button
            type="button"
            disabled={embedIndex.isPending}
            onClick={() => embedIndex.mutate('all')}
          >
            {embedIndex.isPending && embedIndex.variables === 'all' ? '重建中…' : '重建全库索引'}
          </button>
          {embedIndex.isSuccess ? (
            <output className="settings-status settings-status--ok">
              索引完成：{embedIndex.data.indexed} / {embedIndex.data.total}。
            </output>
          ) : null}
          {embedIndex.isError ? (
            <output className="settings-status settings-status--error">{message(embedIndex.error)}</output>
          ) : null}
        </div>
      </section>

      <section className="settings-section" aria-labelledby="settings-maintenance-heading">
        <header>
          <p>MAINTENANCE</p>
          <h2 id="settings-maintenance-heading">数据维护</h2>
          <span>新入库论文不会自动生成中文题名，可在这里一键补齐（新论文入库后语义索引会自动补建）。</span>
        </header>
        <div className="settings-actions">
          <button
            type="button"
            disabled={translateTitles.isPending}
            onClick={() => translateTitles.mutate()}
          >
            {translateTitles.isPending ? '生成中…' : '生成中文题名'}
          </button>
          {titleStatus.data && titleStatus.data.pending > 0 ? (
            <small className="settings-status">待生成 {titleStatus.data.pending} 篇</small>
          ) : null}
          {translateTitles.isSuccess ? (
            <output className="settings-status settings-status--ok">
              已生成 {translateTitles.data.summary.done} / {translateTitles.data.summary.total}
              {translateTitles.data.summary.failed.length > 0
                ? `，失败 ${translateTitles.data.summary.failed.length} 篇`
                : ''}。
            </output>
          ) : null}
          {translateTitles.isError ? (
            <output className="settings-status settings-status--error">{message(translateTitles.error)}</output>
          ) : null}
        </div>
      </section>

      <section className="settings-section" aria-labelledby="settings-pdftext-heading">
        <header>
          <p>PDF TEXT</p>
          <h2 id="settings-pdftext-heading">PDF 文本提取</h2>
          <span>影响讲解 / 翻译的全文读取；默认本地解析，扫描件或排版复杂的 PDF 可改用 OCR 模型 API。</span>
        </header>
        <div className="settings-grid">
          <Field label="提取方式">
            <select
              value={draft.pdfTextProvider}
              onChange={(event) => updateDraft(
                'pdfTextProvider',
                event.currentTarget.value as SettingsDraft['pdfTextProvider'],
              )}
            >
              <option value="default">默认（本地解析）</option>
              <option value="ocr">OCR 模型 API</option>
            </select>
          </Field>
          {draft.pdfTextProvider === 'ocr' ? (
            <>
              <Field label="OCR Base URL" hint="OpenAI 兼容接口根地址，如 https://api.openai.com/v1">
                <input
                  type="url"
                  value={draft.ocrBaseUrl}
                  placeholder="https://api.example.com/v1"
                  onChange={(event) => updateDraft('ocrBaseUrl', event.currentTarget.value)}
                />
              </Field>
              <Field label="OCR Model" hint="支持图片输入的视觉/OCR 模型名">
                <input
                  value={draft.ocrModel}
                  placeholder="如 gpt-4o / qwen-vl-max"
                  onChange={(event) => updateDraft('ocrModel', event.currentTarget.value)}
                />
              </Field>
              <Field label="OCR API Key" hint={secretPlaceholder(settings.hasOcrKey, settings.ocrKeyTail)}>
                <input
                  type="password"
                  autoComplete="new-password"
                  aria-label="OCR API Key"
                  value={secrets.ocrApiKey}
                  placeholder={secretPlaceholder(settings.hasOcrKey, settings.ocrKeyTail)}
                  onChange={(event) => updateSecret('ocrApiKey', event.currentTarget.value)}
                />
              </Field>
            </>
          ) : null}
        </div>
      </section>

      <section className="settings-section" aria-labelledby="settings-obsidian-heading">
        <header>
          <p>OBSIDIAN</p>
          <h2 id="settings-obsidian-heading">单向投影</h2>
        </header>
        <div className="settings-grid">
          <Field label="启用 Obsidian 投影">
            <input
              type="checkbox"
              checked={draft.obsidianEnabled}
              onChange={(event) => updateDraft('obsidianEnabled', event.currentTarget.checked)}
            />
          </Field>
          <Field label="Vault 路径">
            <input
              value={draft.obsidianVaultPath}
              onChange={(event) => updateDraft('obsidianVaultPath', event.currentTarget.value)}
            />
          </Field>
          <Field label="受管根目录">
            <input
              value={draft.obsidianRootFolder}
              onChange={(event) => updateDraft('obsidianRootFolder', event.currentTarget.value)}
            />
          </Field>
          <Field label="PDF 投影模式">
            <select
              value={draft.obsidianPdfMode}
              onChange={(event) => updateDraft(
                'obsidianPdfMode',
                event.currentTarget.value as SettingsDraft['obsidianPdfMode'],
              )}
            >
              <option value="none">不投影</option>
              <option value="reference">引用</option>
              <option value="copy">复制</option>
            </select>
          </Field>
          <Field label="导出 SourceDocument">
            <input
              type="checkbox"
              checked={draft.obsidianExportSource}
              onChange={(event) => updateDraft(
                'obsidianExportSource', event.currentTarget.checked,
              )}
            />
          </Field>
          <Field label="导出讲解">
            <input
              type="checkbox"
              checked={draft.obsidianExportExplainer}
              onChange={(event) => updateDraft(
                'obsidianExportExplainer', event.currentTarget.checked,
              )}
            />
          </Field>
          <Field label="导出翻译">
            <input
              type="checkbox"
              checked={draft.obsidianExportTranslation}
              onChange={(event) => updateDraft(
                'obsidianExportTranslation', event.currentTarget.checked,
              )}
            />
          </Field>
          <Field label="自动导出">
            <input
              type="checkbox"
              checked={draft.obsidianAutoExport}
              onChange={(event) => updateDraft('obsidianAutoExport', event.currentTarget.checked)}
            />
          </Field>
        </div>
        <div className="settings-actions">
          <button
            type="button"
            disabled={!draft.obsidianEnabled || obsidian.testAccess.isPending}
            onClick={() => obsidian.testAccess.mutate()}
          >
            {obsidian.testAccess.isPending ? '正在测试 Obsidian…' : '测试 Obsidian 写权限'}
          </button>
          <button
            type="button"
            disabled={!draft.obsidianEnabled || obsidian.sync.isPending}
            onClick={() => obsidian.sync.mutate({
              dryRun: false,
              applyCleanup: false,
              cleanupPlanSha: null,
            })}
          >
            {obsidian.sync.isPending ? '正在同步到 Obsidian…' : '同步到 Obsidian'}
          </button>
          {obsidian.status.isPending ? (
            <output className="settings-status">正在读取 Obsidian 状态…</output>
          ) : null}
          {obsidian.status.isError ? (
            <output className="settings-status settings-status--error">
              {message(obsidian.status.error)}
            </output>
          ) : null}
          {obsidian.status.data ? (
            <output className="settings-status">
              {obsidian.status.data.enabled ? 'Obsidian 投影已启用。' : 'Obsidian 投影未启用。'}
            </output>
          ) : null}
          {obsidian.testAccess.isSuccess ? (
            <output className={`settings-status ${obsidian.testAccess.data.ok
              ? 'settings-status--ok'
              : 'settings-status--error'}`}
            >
              {obsidian.testAccess.data.ok
                ? 'Obsidian 写权限可用。'
                : 'Obsidian 写权限不可用。'}
            </output>
          ) : null}
          {obsidian.testAccess.isError ? (
            <output className="settings-status settings-status--error">
              {message(obsidian.testAccess.error)}
            </output>
          ) : null}
          {obsidian.sync.isSuccess ? (
            <output className="settings-status settings-status--ok">Obsidian 同步已完成。</output>
          ) : null}
          {obsidian.sync.isError ? (
            <output className="settings-status settings-status--error">
              {message(obsidian.sync.error)}
            </output>
          ) : null}
        </div>
      </section>

      <section className="settings-section" aria-labelledby="settings-appearance-heading">
        <header>
          <p>WORKSPACE</p>
          <h2 id="settings-appearance-heading">本机外观</h2>
          <span>外观偏好只保存在工作区，不会提交到服务器设置。</span>
        </header>
        <div className="settings-density" role="group" aria-label="界面密度">
          <button type="button" aria-pressed={density === 'compact'} onClick={() => setDensity('compact')}>紧凑</button>
          <button type="button" aria-pressed={density === 'comfortable'} onClick={() => setDensity('comfortable')}>舒适</button>
        </div>
      </section>

      <footer className="settings-savebar floating-material">
        <div>
          {save.isSuccess ? <output className="settings-status settings-status--ok">设置已由服务器确认。</output> : null}
          {save.isError ? <output className="settings-status settings-status--error">{message(save.error)}</output> : null}
        </div>
        <button type="submit" className="settings-savebar__primary" disabled={save.isPending}>
          {save.isPending ? '正在保存…' : '保存设置'}
        </button>
      </footer>
    </form>
  );
}

export function Component() {
  const query = useQuery({
    queryKey: settingsKeys.view(),
    queryFn: ({ signal }) => settingsGateway.getSettings(signal),
  });

  if (query.isPending) {
    return <div className="settings-route__state" role="status">正在读取脱敏设置…</div>;
  }
  if (query.isError) {
    return (
      <div className="settings-route__state" role="alert">
        <strong>无法读取设置</strong>
        <span>{message(query.error)}</span>
        <button type="button" onClick={() => void query.refetch()}>重试</button>
      </div>
    );
  }

  const fingerprint = JSON.stringify(query.data);
  return (
    <section className="settings-route" aria-label="设置">
      <header className="settings-route__intro">
        <p>LOCAL CONTROL PLANE</p>
        <h2>配置研究工作区</h2>
        <span>服务器事实与本机外观分开管理，敏感值不会回显。</span>
      </header>
      <SettingsForm key={fingerprint} settings={query.data} />
    </section>
  );
}
