/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button, Checkbox, Input, Select, Surface, Switch } from '@cloudflare/kumo';

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
  // 完成后刷新待生成计数，避免「待生成 N 篇」停留在旧缓存。
  const translateTitles = useMutation({
    mutationFn: () => artifactGateway.translateTitles(0),
    onSuccess: () => queryClient.invalidateQueries({
      queryKey: ['title-translations', 'status'],
    }),
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
      <Surface as="section" className="settings-section" aria-labelledby="settings-model-heading">
        <header>
          <p>GENERATION</p>
          <h2 id="settings-model-heading">生成模型</h2>
          <span>密钥始终保持空白；留空保存不会覆盖现有值。</span>
        </header>
        <div className="settings-grid">
          <Input label="Provider" value={draft.provider} onChange={(event) => updateDraft('provider', (event.target as HTMLInputElement).value)} />
          <Input label="Base URL" type="url" value={draft.baseUrl} onChange={(event) => updateDraft('baseUrl', (event.target as HTMLInputElement).value)} />
          <Input label="Model" value={draft.model} onChange={(event) => updateDraft('model', (event.target as HTMLInputElement).value)} />
          <Input label="API Key" description={secretPlaceholder(settings.hasApiKey, settings.apiKeyTail)} type="password" autoComplete="new-password" aria-label="API Key" value={secrets.apiKey} placeholder={secretPlaceholder(settings.hasApiKey, settings.apiKeyTail)} onChange={(event) => updateSecret('apiKey', (event.target as HTMLInputElement).value)} />
          <Input label="Semantic Scholar Key" description={secretPlaceholder(settings.hasS2Key, settings.s2KeyTail)} type="password" autoComplete="new-password" value={secrets.s2ApiKey} placeholder={secretPlaceholder(settings.hasS2Key, settings.s2KeyTail)} onChange={(event) => updateSecret('s2ApiKey', (event.target as HTMLInputElement).value)} />
        </div>
        <div className="settings-actions">
          <Button type="button" disabled={test.isPending} onClick={() => test.mutate()}>
            {test.isPending ? '正在测试…' : '测试模型连接'}
          </Button>
          {test.isSuccess ? <output className="settings-status settings-status--ok">{test.data.output}</output> : null}
          {test.isError ? <output className="settings-status settings-status--error">{message(test.error)}</output> : null}
        </div>
      </Surface>

      <Surface as="section" className="settings-section" aria-labelledby="settings-storage-heading">
        <header>
          <p>STORAGE</p>
          <h2 id="settings-storage-heading">研究文件</h2>
          <span>相对路径由服务端解析，下面同时显示当前解析结果。</span>
        </header>
        <div className="settings-grid">
          <Input label="PDF 目录" description={`当前解析：${settings.resolvedPdfDir || settings.defaultPdfDir}`} value={draft.pdfDir} onChange={(event) => updateDraft('pdfDir', (event.target as HTMLInputElement).value)} />
          <Input label="讲解目录" description={`当前解析：${settings.resolvedExplainerDir || settings.defaultExplainerDir}`} value={draft.explainerDir} onChange={(event) => updateDraft('explainerDir', (event.target as HTMLInputElement).value)} />
          <Input label="翻译目录" description={`当前解析：${settings.resolvedTranslationDir || settings.defaultTranslationDir}`} value={draft.translationDir} onChange={(event) => updateDraft('translationDir', (event.target as HTMLInputElement).value)} />
          <Input label="研究主题" value={draft.researchTheme} onChange={(event) => updateDraft('researchTheme', (event.target as HTMLInputElement).value)} />
        </div>
      </Surface>

      <Surface as="section" className="settings-section" aria-labelledby="settings-embed-heading">
        <header>
          <p>EMBEDDINGS</p>
          <h2 id="settings-embed-heading">语义索引</h2>
        </header>
        <div className="settings-grid">
          <Input label="Embedding Provider" value={draft.embedProvider} onChange={(event) => updateDraft('embedProvider', (event.target as HTMLInputElement).value)} />
          <Input label="Embedding Base URL" type="url" value={draft.embedApiBase} onChange={(event) => updateDraft('embedApiBase', (event.target as HTMLInputElement).value)} />
          <Input label="Embedding Model" value={draft.embedApiModel} onChange={(event) => updateDraft('embedApiModel', (event.target as HTMLInputElement).value)} />
          <Input label="Embedding API Key" description={secretPlaceholder(settings.hasEmbedKey, settings.embedKeyTail)} type="password" autoComplete="new-password" value={secrets.embedApiKey} placeholder={secretPlaceholder(settings.hasEmbedKey, settings.embedKeyTail)} onChange={(event) => updateSecret('embedApiKey', (event.target as HTMLInputElement).value)} />
        </div>
        <div className="settings-actions">
          <Button
            type="button"
            disabled={embedIndex.isPending}
            onClick={() => embedIndex.mutate('missing')}
          >
            {embedIndex.isPending && embedIndex.variables === 'missing' ? '索引中…' : '补齐缺失索引'}
          </Button>
          <Button
            type="button"
            disabled={embedIndex.isPending}
            onClick={() => embedIndex.mutate('all')}
          >
            {embedIndex.isPending && embedIndex.variables === 'all' ? '重建中…' : '重建全库索引'}
          </Button>
          {embedIndex.isSuccess ? (
            <output className="settings-status settings-status--ok">
              索引完成：{embedIndex.data.indexed} / {embedIndex.data.total}。
            </output>
          ) : null}
          {embedIndex.isError ? (
            <output className="settings-status settings-status--error">{message(embedIndex.error)}</output>
          ) : null}
        </div>
      </Surface>

      <Surface as="section" className="settings-section" aria-labelledby="settings-maintenance-heading">
        <header>
          <p>MAINTENANCE</p>
          <h2 id="settings-maintenance-heading">数据维护</h2>
          <span>新入库论文不会自动生成中文题名，可在这里一键补齐（新论文入库后语义索引会自动补建）。</span>
        </header>
        <div className="settings-actions">
          <Button
            type="button"
            disabled={translateTitles.isPending}
            onClick={() => translateTitles.mutate()}
          >
            {translateTitles.isPending ? '生成中…' : '生成中文题名'}
          </Button>
          {titleStatus.data && titleStatus.data.pending > 0 ? (
            <small className="settings-status">待生成 {titleStatus.data.pending} 篇</small>
          ) : null}
          {translateTitles.isSuccess ? (() => {
            const summary = translateTitles.data.summary;
            const failedTitles = summary.failed
              .map((failure) => failure.title || failure.id)
              .join('、');
            const tone = summary.failed.length === 0
              ? 'settings-status--ok'
              : summary.done > 0
                ? 'settings-status--ok'
                : 'settings-status--error';
            return (
              <output className={`settings-status ${tone}`}>
                已生成 {summary.done} / {summary.total}
                {summary.failed.length > 0
                  ? `，失败 ${summary.failed.length} 篇（${failedTitles}）——题名若为纯专有名词/缩写则无中文可译`
                  : ''}。
              </output>
            );
          })() : null}
          {translateTitles.isError ? (
            <output className="settings-status settings-status--error">{message(translateTitles.error)}</output>
          ) : null}
        </div>
      </Surface>

      <Surface as="section" className="settings-section" aria-labelledby="settings-pdftext-heading">
        <header>
          <p>PDF TEXT</p>
          <h2 id="settings-pdftext-heading">PDF 文本提取</h2>
          <span>影响讲解 / 翻译的全文读取；默认本地解析，扫描件或排版复杂的 PDF 可改用 OCR 模型 API。</span>
        </header>
        <div className="settings-grid">
          <Select
            label="提取方式"
            value={draft.pdfTextProvider}
            onValueChange={(value) => updateDraft('pdfTextProvider', value as SettingsDraft['pdfTextProvider'])}
          >
            <Select.Option value="default">默认（本地解析）</Select.Option>
            <Select.Option value="ocr">OCR 模型 API</Select.Option>
          </Select>
          {draft.pdfTextProvider === 'ocr' ? (
            <>
              <Input label="OCR Base URL" description="OpenAI 兼容接口根地址，如 https://api.openai.com/v1" type="url" value={draft.ocrBaseUrl} placeholder="https://api.example.com/v1" onChange={(event) => updateDraft('ocrBaseUrl', (event.target as HTMLInputElement).value)} />
              <Input label="OCR Model" description="支持图片输入的视觉/OCR 模型名" value={draft.ocrModel} placeholder="如 gpt-4o / qwen-vl-max" onChange={(event) => updateDraft('ocrModel', (event.target as HTMLInputElement).value)} />
              <Input label="OCR API Key" description={secretPlaceholder(settings.hasOcrKey, settings.ocrKeyTail)} type="password" autoComplete="new-password" aria-label="OCR API Key" value={secrets.ocrApiKey} placeholder={secretPlaceholder(settings.hasOcrKey, settings.ocrKeyTail)} onChange={(event) => updateSecret('ocrApiKey', (event.target as HTMLInputElement).value)} />
            </>
          ) : null}
        </div>
      </Surface>

      <Surface as="section" className="settings-section" aria-labelledby="settings-obsidian-heading">
        <header>
          <p>OBSIDIAN</p>
          <h2 id="settings-obsidian-heading">单向投影</h2>
        </header>
        <div className="settings-grid">
          <Switch label="启用 Obsidian 投影" checked={draft.obsidianEnabled} onCheckedChange={(checked) => updateDraft('obsidianEnabled', checked)} />
          <Input label="Vault 路径" value={draft.obsidianVaultPath} onChange={(event) => updateDraft('obsidianVaultPath', (event.target as HTMLInputElement).value)} />
          <Input label="受管根目录" value={draft.obsidianRootFolder} onChange={(event) => updateDraft('obsidianRootFolder', (event.target as HTMLInputElement).value)} />
          <Select
            label="PDF 投影模式"
            value={draft.obsidianPdfMode}
            onValueChange={(value) => updateDraft('obsidianPdfMode', value as SettingsDraft['obsidianPdfMode'])}
          >
            <Select.Option value="none">不投影</Select.Option>
            <Select.Option value="reference">引用</Select.Option>
            <Select.Option value="copy">复制</Select.Option>
          </Select>
          <Checkbox label="导出 SourceDocument" checked={draft.obsidianExportSource} onCheckedChange={(checked) => updateDraft('obsidianExportSource', checked)} />
          <Checkbox label="导出讲解" checked={draft.obsidianExportExplainer} onCheckedChange={(checked) => updateDraft('obsidianExportExplainer', checked)} />
          <Checkbox label="导出翻译" checked={draft.obsidianExportTranslation} onCheckedChange={(checked) => updateDraft('obsidianExportTranslation', checked)} />
          <Switch label="自动导出" checked={draft.obsidianAutoExport} onCheckedChange={(checked) => updateDraft('obsidianAutoExport', checked)} />
        </div>
        <div className="settings-actions">
          <Button
            type="button"
            disabled={!draft.obsidianEnabled || obsidian.testAccess.isPending}
            onClick={() => obsidian.testAccess.mutate()}
          >
            {obsidian.testAccess.isPending ? '正在测试 Obsidian…' : '测试 Obsidian 写权限'}
          </Button>
          <Button
            type="button"
            disabled={!draft.obsidianEnabled || obsidian.sync.isPending}
            onClick={() => obsidian.sync.mutate({
              dryRun: false,
              applyCleanup: false,
              cleanupPlanSha: null,
            })}
          >
            {obsidian.sync.isPending ? '正在同步到 Obsidian…' : '同步到 Obsidian'}
          </Button>
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
      </Surface>

      <Surface as="section" className="settings-section" aria-labelledby="settings-appearance-heading">
        <header>
          <p>WORKSPACE</p>
          <h2 id="settings-appearance-heading">本机外观</h2>
          <span>外观偏好只保存在工作区，不会提交到服务器设置。</span>
        </header>
        <div className="settings-density" role="group" aria-label="界面密度">
          <Button type="button" variant={density === 'compact' ? 'primary' : 'ghost'} onClick={() => setDensity('compact')}>紧凑</Button>
          <Button type="button" variant={density === 'comfortable' ? 'primary' : 'ghost'} onClick={() => setDensity('comfortable')}>舒适</Button>
        </div>
      </Surface>

      <footer className="settings-savebar floating-material">
        <div>
          {save.isSuccess ? <output className="settings-status settings-status--ok">设置已由服务器确认。</output> : null}
          {save.isError ? <output className="settings-status settings-status--error">{message(save.error)}</output> : null}
        </div>
        <Button type="submit" variant="primary" className="settings-savebar__primary" disabled={save.isPending}>
          {save.isPending ? '正在保存…' : '保存设置'}
        </Button>
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
        <Button type="button" onClick={() => void query.refetch()}>重试</Button>
      </div>
    );
  }

  const fingerprint = JSON.stringify(query.data);
  return (
    <section className="settings-route" aria-label="设置">
      {/* 页标题已在顶部命令栏展示；原大块 intro 移除，把纵向空间让给设置表单。 */}
      <SettingsForm key={fingerprint} settings={query.data} />
    </section>
  );
}
