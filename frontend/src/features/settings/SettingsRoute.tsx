/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import { useState, type FormEvent, type ReactNode } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { settingsKeys } from '../../lib/api/keys';
import type { SettingsUpdate, SettingsView } from '../../lib/api/types';
import { settingsGateway } from '../../lib/api/settingsGateway';
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

  const updateDraft = (field: keyof SettingsDraft, value: string) => {
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
