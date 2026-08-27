import { useCallback, useEffect, useState, type ReactNode } from 'react';

import { settingsApi, v2Api } from '../api/client';
import type { Settings } from '../api/types';
import { formatLlmConnectionResult } from './settingsFeedback';

interface SettingsPageProps {
  notify: (message: string) => void;
}

type DraftValue = string | number | boolean;

const NAV = [
  { id: 'sec-llm', label: 'LLM 模型' },
  { id: 'sec-keys', label: 'API 凭据' },
  { id: 'sec-embed', label: '语义检索' },
  { id: 'sec-s2', label: 'Semantic Scholar' },
  { id: 'sec-ocr', label: 'OCR 与提取' },
  { id: 'sec-llm-pipe', label: '大模型与翻译管道' },
  { id: 'sec-dirs', label: '数据目录' },
  { id: 'sec-obsidian', label: 'Obsidian' },
  { id: 'sec-system', label: '系统状态' },
];

/* ── 布局基元 ─────────────────────────────────── */

function Section({
  id,
  title,
  desc,
  children,
  foot,
}: {
  id: string;
  title: string;
  desc: string;
  children: ReactNode;
  foot?: ReactNode;
}) {
  return (
    <section className="card settings-section" id={id} aria-labelledby={`${id}-title`}>
      <header className="settings-section__head">
        <h3 id={`${id}-title`}>{title}</h3>
        <small>{desc}</small>
      </header>
      <div className="settings-section__body">{children}</div>
      {foot}
    </section>
  );
}

function Row({
  title,
  desc,
  children,
  isSwitch,
  className,
}: {
  title: string;
  desc?: string;
  children: ReactNode;
  isSwitch?: boolean;
  className?: string;
}) {
  return (
    <div
      className={`settings-row${isSwitch ? ' settings-row--switch' : ''}${className ? ` ${className}` : ''}`}
    >
      <span className="settings-row__label">
        <strong>{title}</strong>
        {desc && <small>{desc}</small>}
      </span>
      <div className="settings-row__control">{children}</div>
    </div>
  );
}

export function SettingsPage({ notify }: SettingsPageProps) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<Record<string, DraftValue>>({});
  const [keys, setKeys] = useState({ apiKey: '', ocrApiKey: '', embedApiKey: '', s2ApiKey: '' });
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState('');
  const [testingLlm, setTestingLlm] = useState(false);
  const [clearingKey, setClearingKey] = useState<string | null>(null);
  const [llmResult, setLlmResult] = useState('');
  const [activeSection, setActiveSection] = useState(NAV[0].id);
  const [obsidian, setObsidian] = useState<{
    enabled: boolean;
    vaultConfigured: boolean;
    writable: boolean;
  } | null>(null);
  const [health, setHealth] = useState<{ v2?: string; ready?: string }>({});

  const set = (field: string) => (value: DraftValue) =>
    setDraft((prev) => ({ ...prev, [field]: value }));

  const load = useCallback(async () => {
    setLoadError('');
    try {
      const data = await settingsApi.get();
      setSettings(data);
      setDraft({
        provider: data.provider,
        baseUrl: data.baseUrl,
        model: data.model,
        llmTimeout: data.llmTimeout,
        researchTheme: data.researchTheme,
        ocrEnabled: data.ocrEnabled,
        explainMaxChars: data.explainMaxChars,
        translateMode: data.translateMode,
        translateChunkSize: data.translateChunkSize,
        translateMaxChars: data.translateMaxChars,
        translateWorkers: data.translateWorkers,
        translateSkipReferences: data.translateSkipReferences ?? true,
        ocrProvider: data.ocrProvider,
        ocrBaseUrl: data.ocrBaseUrl,
        ocrModel: data.ocrModel,
        ocrTimeout: data.ocrTimeout,
        ocrPageBatchSize: data.ocrPageBatchSize,
        ocrMaxConcurrency: data.ocrMaxConcurrency,
        pdfTextProvider: data.pdfTextProvider,
        embedProvider: data.embedProvider,
        embedApiBase: data.embedApiBase,
        embedApiModel: data.embedApiModel,
        s2Provider: data.s2Provider,
        s2Endpoint: data.s2Endpoint,
        pdfDir: data.pdfDir,
        explainerDir: data.explainerDir,
        translationDir: data.translationDir,
        ocrMarkdownDir: data.ocrMarkdownDir,
        reproductionDir: data.reproductionDir,
        obsidianEnabled: data.obsidianEnabled,
        obsidianVaultPath: data.obsidianVaultPath,
        obsidianRootFolder: data.obsidianRootFolder,
        obsidianPdfMode: data.obsidianPdfMode,
        obsidianExportSource: data.obsidianExportSource,
        obsidianExportExplainer: data.obsidianExportExplainer,
        obsidianExportTranslation: data.obsidianExportTranslation,
        obsidianAutoExport: data.obsidianAutoExport,
      });
    } catch (error: unknown) {
      setLoadError(error instanceof Error ? error.message : String(error));
    }
    v2Api
      .obsidianStatus()
      .then((status) => setObsidian(status))
      .catch(() => setObsidian(null));
    v2Api
      .health()
      .then((result) => setHealth((prev) => ({ ...prev, v2: result.schemaRevision })))
      .catch(() => setHealth((prev) => ({ ...prev, v2: '不可用' })));
    v2Api
      .readiness()
      .then((result) => setHealth((prev) => ({ ...prev, ready: result.status })))
      .catch(() => setHealth((prev) => ({ ...prev, ready: '不可用' })));
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  /* 滚动联动：目录随当前可见分区高亮（分区渲染于 settings 加载后） */
  useEffect(() => {
    if (settings === null) return;
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length > 0) setActiveSection(visible[0].target.id);
      },
      { rootMargin: '-90px 0px -55% 0px', threshold: 0 },
    );
    for (const item of NAV) {
      const element = document.getElementById(item.id);
      if (element) observer.observe(element);
    }
    /* 到底时最后一个分区顶部可能永远进不了观测带，用 scroll 监听强制高亮 */
    const onScroll = () => {
      const atBottom =
        window.innerHeight + window.scrollY >=
        document.documentElement.scrollHeight - 4;
      if (atBottom) setActiveSection(NAV[NAV.length - 1].id);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      observer.disconnect();
      window.removeEventListener('scroll', onScroll);
    };
  }, [settings]);

  const save = async () => {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = { ...draft };
      for (const [field, value] of Object.entries(keys)) {
        if (value.trim()) payload[field] = value.trim();
      }
      const result = await settingsApi.update(payload);
      if (result.ok) {
        const touchedKeys = Object.values(keys).some((value) => value.trim());
        setKeys({ apiKey: '', ocrApiKey: '', embedApiKey: '', s2ApiKey: '' });
        notify(touchedKeys ? '设置已保存（含 API Key 更新）' : '设置已保存');
        await load();
      } else {
        notify(`保存失败：${result.error ?? '未知错误'}`);
      }
    } catch (error) {
      notify(`保存失败：${error instanceof Error ? error.message : error}`);
    } finally {
      setSaving(false);
    }
  };

  const clearKey = async (kind: string, label: string) => {
    if (!window.confirm(`确认清除「${label}」的已保存凭据？`)) return;
    setClearingKey(kind);
    try {
      const result = await settingsApi.update({ clearCredentials: [kind] });
      if (result.ok) {
        notify(`已清除「${label}」凭据`);
        await load();
      } else {
        notify(`清除失败：${result.error ?? '未知错误'}`);
      }
    } catch (error) {
      notify(`清除失败：${error instanceof Error ? error.message : error}`);
    } finally {
      setClearingKey(null);
    }
  };

  const testLlm = async () => {
    setTestingLlm(true);
    setLlmResult('测试中…');
    try {
      const result = await settingsApi.testLlm();
      setLlmResult(formatLlmConnectionResult(result));
    } catch (error: unknown) {
      setLlmResult(`失败：${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setTestingLlm(false);
    }
  };

  if (!settings) {
    return (
      <div className="page page-enter settings">
        <div className={`card reviews__placeholder${loadError ? ' reader__empty--error' : ''}`} role={loadError ? 'alert' : 'status'}>
          {loadError ? (
            <>
              <p>设置加载失败：{loadError}</p>
              <button type="button" className="btn btn--sm" onClick={() => void load()}>
                重新加载
              </button>
            </>
          ) : (
            '正在加载设置（GET /api/settings）…'
          )}
        </div>
      </div>
    );
  }

  const credentialCards: Array<{
    kind: string;
    label: string;
    hasKey: boolean;
    tail: string;
    field: keyof typeof keys;
    hint: string;
  }> = [
    { kind: 'llm', label: 'LLM', hasKey: settings.hasApiKey, tail: settings.apiKeyTail, field: 'apiKey', hint: '讲解 / 翻译 / 推荐' },
    { kind: 'ocr', label: 'OCR', hasKey: settings.hasOcrKey, tail: settings.ocrKeyTail, field: 'ocrApiKey', hint: '扫描版 PDF 识别' },
    { kind: 'embedding', label: 'Embedding', hasKey: settings.hasEmbedKey, tail: settings.embedKeyTail, field: 'embedApiKey', hint: '语义检索嵌入' },
    { kind: 'semantic_scholar', label: 'Semantic Scholar', hasKey: settings.hasS2Key, tail: settings.s2KeyTail, field: 's2ApiKey', hint: '采集与引用数据' },
  ];

  return (
    <div className="page page-enter settings">
      {loadError && (
        <div className="card reader__empty--error" role="alert">
          <p>设置刷新失败：{loadError}</p>
          <button type="button" className="btn btn--sm" onClick={() => void load()}>
            重试
          </button>
        </div>
      )}
      <div className="settings-layout">
        <div className="settings-sections">
          <Section id="sec-llm" title="LLM 模型" desc="讲解、翻译、推荐与采集增强所用的大模型">
            <Row title="Provider">
              <select
                className="input"
                aria-label="LLM Provider"
                value={String(draft.provider ?? '')}
                onChange={(event) => set('provider')(event.target.value)}
              >
                {['deepseek', 'openai', 'anthropic', 'ollama', 'other'].map((value) => (
                  <option key={value} value={value}>
                    {value}
                  </option>
                ))}
              </select>
            </Row>
            <Row title="Base URL" desc="OpenAI 兼容接口地址">
              <input
                className="input"
                aria-label="Base URL"
                value={String(draft.baseUrl ?? '')}
                onChange={(event) => set('baseUrl')(event.target.value)}
              />
            </Row>
            <Row title="模型">
              <input
                className="input"
                aria-label="模型"
                value={String(draft.model ?? '')}
                onChange={(event) => set('model')(event.target.value)}
              />
            </Row>
            <Row title="超时" desc="单位毫秒；0＝使用 SDK 默认超时">
              <input
                className="input"
                type="number"
                aria-label="LLM 超时"
                value={Number(draft.llmTimeout ?? 60000)}
                min={0}
                onChange={(event) => set('llmTimeout')(event.target.value === '' ? 0 : Number(event.target.value))}
              />
            </Row>
            <Row title="研究方向主题" desc="采集页默认检索方向">
              <input
                className="input"
                aria-label="研究方向主题"
                value={String(draft.researchTheme ?? '')}
                onChange={(event) => set('researchTheme')(event.target.value)}
              />
            </Row>
            <Row title="连通性测试">
              <button type="button" className="btn btn--sm" onClick={() => void testLlm()} disabled={testingLlm} aria-busy={testingLlm}>
                {testingLlm ? '测试中…' : '测试模型连通'}
              </button>
              {llmResult && (
                <span className={`badge ${llmResult === '连通正常' ? 'badge--jade' : 'badge--seal'}`} role="status" aria-live="polite">
                  {llmResult}
                </span>
              )}
            </Row>
          </Section>

          <Section
            id="sec-keys"
            title="API 凭据"
            desc="密钥仅保存在本机；留空提交保持原值，填写则覆盖"
          >
            <div className="settings-section__body--padded settings__keys">
              {credentialCards.map((card) => (
                <div className="settings__key-card" key={card.kind}>
                  <header>
                    <strong>{card.label}</strong>
                    {card.hasKey ? (
                      <span className="badge badge--jade">已配置 {card.tail}</span>
                    ) : (
                      <span className="badge badge--amber">未配置</span>
                    )}
                  </header>
                  <small>{card.hint}</small>
                  <div className="settings__key-row">
                    <input
                      className="input"
                      type="password"
                      autoComplete="off"
                      aria-label={`${card.label} API Key`}
                      placeholder={card.hasKey ? '留空保持不变' : `输入 ${card.label} API Key…`}
                      value={keys[card.field]}
                      onChange={(event) =>
                        setKeys((prev) => ({ ...prev, [card.field]: event.target.value }))
                      }
                    />
                    {card.hasKey && (
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        disabled={clearingKey !== null}
                        aria-busy={clearingKey === card.kind}
                        onClick={() => void clearKey(card.kind, card.label)}
                      >
                        {clearingKey === card.kind ? '清除中…' : '清除'}
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </Section>

          <Section id="sec-embed" title="语义检索（Embedding）" desc="驱动语义检索与分块索引">
            <Row title="Provider">
              <select
                className="input"
                aria-label="Embedding Provider"
                value={String(draft.embedProvider ?? '')}
                onChange={(event) => set('embedProvider')(event.target.value)}
              >
                <option value="local">local（本地模型）</option>
                <option value="api">api（远程 API）</option>
              </select>
            </Row>
            <Row title="API Base" desc="如 SiliconFlow / OpenAI 兼容端点">
              <input
                className="input"
                aria-label="Embedding API Base"
                value={String(draft.embedApiBase ?? '')}
                onChange={(event) => set('embedApiBase')(event.target.value)}
              />
            </Row>
            <Row title="嵌入模型">
              <input
                className="input"
                aria-label="嵌入模型"
                value={String(draft.embedApiModel ?? '')}
                onChange={(event) => set('embedApiModel')(event.target.value)}
              />
            </Row>
          </Section>

          <Section id="sec-s2" title="Semantic Scholar" desc="文献采集与引用图谱数据源">
            <Row title="Provider">
              <input
                className="input"
                aria-label="S2 Provider"
                value={String(draft.s2Provider ?? '')}
                onChange={(event) => set('s2Provider')(event.target.value)}
              />
            </Row>
            <Row title="Graph Endpoint">
              <input
                className="input"
                aria-label="S2 Endpoint"
                value={String(draft.s2Endpoint ?? '')}
                onChange={(event) => set('s2Endpoint')(event.target.value)}
              />
            </Row>
          </Section>

          <Section id="sec-ocr" title="OCR 与 PDF 文本提取" desc="扫描版 PDF 的文字识别通道">
            <Row
              title="启用 OCR 提取"
              desc="开启后讲解/翻译全文只使用 OCR 模型，失败不会改用本地解析；关闭则使用本地解析"
              isSwitch
            >
              <input
                type="checkbox"
                className="settings-switch"
                aria-label="启用 OCR 提取"
                checked={String(draft.pdfTextProvider ?? 'default') === 'ocr'}
                onChange={(event) => set('pdfTextProvider')(event.target.checked ? 'ocr' : 'default')}
              />
            </Row>
            <Row title="文本提取方式（兼容项）">
              <select
                className="input"
                aria-label="PDF 文本提取"
                value={String(draft.pdfTextProvider ?? 'default')}
                onChange={(event) => set('pdfTextProvider')(event.target.value)}
              >
                <option value="default">default（本地解析）</option>
                <option value="ocr">ocr（OCR 模型 API）</option>
              </select>
            </Row>
            <Row title="启用 OCR 通道" isSwitch>
              <input
                type="checkbox"
                className="settings-switch"
                aria-label="启用 OCR 通道"
                checked={Boolean(draft.ocrEnabled)}
                onChange={(event) => set('ocrEnabled')(event.target.checked)}
              />
            </Row>
            <Row title="OCR Provider">
              <input
                className="input"
                aria-label="OCR Provider"
                value={String(draft.ocrProvider ?? '')}
                onChange={(event) => set('ocrProvider')(event.target.value)}
              />
            </Row>
            <Row title="OCR Base URL">
              <input
                className="input"
                aria-label="OCR Base URL"
                value={String(draft.ocrBaseUrl ?? '')}
                onChange={(event) => set('ocrBaseUrl')(event.target.value)}
              />
            </Row>
            <Row title="OCR 模型">
              <input
                className="input"
                aria-label="OCR 模型"
                value={String(draft.ocrModel ?? '')}
                onChange={(event) => set('ocrModel')(event.target.value)}
              />
            </Row>
            <Row title="超时" desc="单位毫秒">
              <input
                className="input"
                type="number"
                aria-label="OCR 超时"
                value={Number(draft.ocrTimeout ?? 60000)}
                onChange={(event) => set('ocrTimeout')(Number(event.target.value) || 60000)}
              />
            </Row>
            <Row title="每批页数" desc="1–16">
              <input
                className="input"
                type="number"
                aria-label="OCR 每批页数"
                value={Number(draft.ocrPageBatchSize ?? 4)}
                onChange={(event) => set('ocrPageBatchSize')(Number(event.target.value) || 4)}
              />
            </Row>
            <Row title="并发数" desc="1–4">
              <input
                className="input"
                type="number"
                aria-label="OCR 并发数"
                value={Number(draft.ocrMaxConcurrency ?? 2)}
                onChange={(event) => set('ocrMaxConcurrency')(Number(event.target.value) || 2)}
              />
            </Row>
          </Section>

          <Section
            id="sec-llm-pipe"
            title="大模型与翻译管道"
            desc="讲解/翻译的全文读取与生成策略，修改后对新生成生效"
          >
            <Row
              title="讲解全文字符上限"
              desc="讲解管道读全文的截断上限；模型上下文 1M 时可设为 1,000,000"
            >
              <input
                className="input"
                type="number"
                aria-label="讲解全文字符上限"
                min={20000}
                step={10000}
                value={Number(draft.explainMaxChars ?? 120000)}
                onChange={(event) => set('explainMaxChars')(Number(event.target.value) || 120000)}
              />
            </Row>
            <Row title="LLM 请求超时" desc="单位毫秒；讲解/翻译等生成调用的请求超时，0＝用 SDK 默认">
              <input
                className="input"
                type="number"
                aria-label="LLM 请求超时"
                min={0}
                step={30}
                value={Number(draft.llmTimeout ?? 0)}
                onChange={(event) => set('llmTimeout')(Number(event.target.value) || 0)}
              />
            </Row>
            <Row title="翻译模式" desc="分块＝逐块并发翻译（稳，默认）；整篇＝全文一次送入（需大上下文模型）">
              <select
                className="input"
                aria-label="翻译模式"
                value={String(draft.translateMode ?? 'chunked')}
                onChange={(event) => set('translateMode')(event.target.value)}
              >
                <option value="chunked">chunked（分块并发翻译）</option>
                <option value="full">full（整篇一次翻译）</option>
              </select>
            </Row>
            <Row
              title="分块大小"
              desc={
                String(draft.translateMode ?? 'chunked') === 'full'
                  ? '整篇模式下不生效'
                  : '每块字符数，按标题/段落边界切块（仅分块模式生效）'
              }
            >
              <input
                className="input"
                type="number"
                aria-label="分块大小"
                min={500}
                step={100}
                value={Number(draft.translateChunkSize ?? 3800)}
                onChange={(event) => set('translateChunkSize')(Number(event.target.value) || 3800)}
              />
            </Row>
            <Row
              title="翻译全文字符上限"
              desc="独立于讲解截断；默认同讲解上限，可按模型上下文单独调高"
            >
              <input
                className="input"
                type="number"
                aria-label="翻译全文字符上限"
                min={20000}
                step={10000}
                value={Number(draft.translateMaxChars ?? 120000)}
                onChange={(event) => set('translateMaxChars')(Number(event.target.value) || 120000)}
              />
            </Row>
            <Row title="分块翻译并发数" desc="同时发起的翻译请求数（仅分块模式生效，建议 1–6）">
              <input
                className="input"
                type="number"
                aria-label="分块翻译并发数"
                min={1}
                max={8}
                step={1}
                value={Number(draft.translateWorkers ?? 4)}
                onChange={(event) => set('translateWorkers')(Math.min(8, Math.max(1, Number(event.target.value) || 4)))}
              />
            </Row>
            <Row title="跳过参考文献" desc="翻译前剔除参考文献/致谢章节（默认开启，节省 token）" isSwitch>
              <input
                type="checkbox"
                className="settings-switch"
                aria-label="跳过参考文献"
                checked={Boolean(draft.translateSkipReferences ?? true)}
                onChange={(event) => set('translateSkipReferences')(event.target.checked)}
              />
            </Row>
          </Section>

          <Section id="sec-dirs" title="数据目录" desc="统一管理文献产物与论文复现附件的本地存储位置">
            <Row title="PDF 目录" desc={`当前：${settings.resolvedPdfDir}`}>
              <input
                className="input"
                aria-label="PDF 目录"
                value={String(draft.pdfDir ?? '')}
                placeholder={settings.defaultPdfDir}
                onChange={(event) => set('pdfDir')(event.target.value)}
              />
            </Row>
            <Row title="讲解目录" desc={`当前：${settings.resolvedExplainerDir}`}>
              <input
                className="input"
                aria-label="讲解目录"
                value={String(draft.explainerDir ?? '')}
                placeholder={settings.defaultExplainerDir}
                onChange={(event) => set('explainerDir')(event.target.value)}
              />
            </Row>
            <Row title="翻译目录" desc={`当前：${settings.resolvedTranslationDir}`}>
              <input
                className="input"
                aria-label="翻译目录"
                value={String(draft.translationDir ?? '')}
                placeholder={settings.defaultTranslationDir}
                onChange={(event) => set('translationDir')(event.target.value)}
              />
            </Row>
            <Row
              title="OCR Markdown 目录"
              desc={`当前：${settings.resolvedOcrMarkdownDir ?? '（后端重启后生效）'}，留空＝默认 data/ocr_markdown`}
            >
              <input
                className="input"
                aria-label="OCR Markdown 目录"
                value={String(draft.ocrMarkdownDir ?? '')}
                placeholder={settings.defaultOcrMarkdownDir ?? 'data/ocr_markdown'}
                onChange={(event) => set('ocrMarkdownDir')(event.target.value)}
              />
            </Row>
            <Row
              title="论文复现目录"
              desc={`当前：${settings.resolvedReproductionDir ?? '（后端重启后生效）'} · 保存后重启后端生效 · 留空＝默认 data/reproduction-artifacts`}
              className="settings-row--reproduction"
            >
              <input
                className="input"
                aria-label="论文复现目录"
                value={String(draft.reproductionDir ?? '')}
                placeholder={settings.defaultReproductionDir ?? 'data/reproduction-artifacts'}
                onChange={(event) => set('reproductionDir')(event.target.value)}
              />
            </Row>
          </Section>

          <Section
            id="sec-obsidian"
            title="Obsidian 投影"
            desc="将文献与讲解导出到 Obsidian Vault"
            foot={
              <div className="settings__savebar">
                <small>
                  {obsidian
                    ? `后端：${obsidian.enabled ? '已启用' : '未启用'} · ${obsidian.vaultConfigured ? 'Vault 已配置' : 'Vault 未配置'} · ${obsidian.writable ? '可写' : '只读'}`
                    : 'Obsidian 状态加载中…'}
                </small>
                <div className="deep__actions">
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={async () => {
                      const result = await v2Api.obsidianTest().catch(() => ({ ok: false }));
                      notify(result.ok ? 'Obsidian 访问正常' : '访问失败（检查 Vault 路径）');
                    }}
                  >
                    测试访问
                  </button>
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={async () => {
                      try {
                        await v2Api.obsidianSync(false);
                        notify('Obsidian 同步任务已创建');
                        await load();
                      } catch (error) {
                        notify(`同步失败：${error instanceof Error ? error.message : error}`);
                      }
                    }}
                  >
                    立即同步
                  </button>
                </div>
              </div>
            }
          >
            <Row title="Vault 路径" desc="必须为绝对路径">
              <input
                className="input"
                aria-label="Vault 路径"
                value={String(draft.obsidianVaultPath ?? '')}
                onChange={(event) => set('obsidianVaultPath')(event.target.value)}
              />
            </Row>
            <Row title="受管根目录" desc="Vault 内的相对目录">
              <input
                className="input"
                aria-label="受管根目录"
                value={String(draft.obsidianRootFolder ?? '')}
                onChange={(event) => set('obsidianRootFolder')(event.target.value)}
              />
            </Row>
            <Row title="PDF 投影模式">
              <select
                className="input"
                aria-label="PDF 投影模式"
                value={String(draft.obsidianPdfMode ?? 'none')}
                onChange={(event) => set('obsidianPdfMode')(event.target.value)}
              >
                <option value="none">不投影</option>
                <option value="reference">引用</option>
                <option value="copy">复制</option>
              </select>
            </Row>
            {(
              [
                ['obsidianEnabled', '启用投影'],
                ['obsidianExportSource', '导出源文档'],
                ['obsidianExportExplainer', '导出讲解'],
                ['obsidianExportTranslation', '导出翻译'],
                ['obsidianAutoExport', '完成后自动导出'],
              ] as Array<[string, string]>
            ).map(([field, label]) => (
              <Row key={field} title={label} isSwitch>
                <input
                  type="checkbox"
                  className="settings-switch"
                  aria-label={label}
                  checked={Boolean(draft[field])}
                  onChange={(event) => set(field)(event.target.checked)}
                />
              </Row>
            ))}
          </Section>

          <Section
            id="sec-system"
            title="系统状态"
            desc="后端健康检查与整体保存"
            foot={
              <div className="settings__savebar">
                <small>
                  保存调用 POST /api/settings：配置写入 data/settings.json，
                  填写的 API Key 写入本机凭据存储，留空保持原值。
                </small>
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => void save()}
                  disabled={saving}
                  aria-busy={saving}
                >
                  {saving ? '保存中…' : '保存全部设置'}
                </button>
              </div>
            }
          >
            <Row title="v2 API 健康" desc="/api/v2/health">
              <span className="badge badge--venue">{health.v2 ?? '…'}</span>
              <button type="button" className="btn btn--sm" onClick={() => void load()}>
                刷新
              </button>
            </Row>
            <Row title="就绪检查" desc="/health/ready">
              <span className={`badge ${health.ready === 'ready' ? 'badge--jade' : 'badge--amber'}`}>
                {health.ready ?? '…'}
              </span>
            </Row>
          </Section>
        </div>

        <nav className="settings-nav" aria-label="设置目录">
          {NAV.map((item) => (
            <a
              key={item.id}
              className={`settings-nav__item${activeSection === item.id ? ' settings-nav__item--active' : ''}`}
              href={`#${item.id}`}
              aria-current={activeSection === item.id ? 'true' : undefined}
            >
              {item.label}
            </a>
          ))}
        </nav>
      </div>
    </div>
  );
}
