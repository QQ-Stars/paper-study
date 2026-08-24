import { useEffect, useMemo, useState } from 'react';

import { acquireApi, artifactApi, libraryApi, maintenanceApi } from '../api/client';
import type { BatchRun, DuplicatePair, EnrichStatus, Paper, StudyStatus } from '../api/types';
import { DeleteConfirmDialog } from './DeleteConfirmDialog';
import { PlusIcon, SearchIcon } from './Icons';
import { StreamConsole, useStream } from './StreamConsole';
import {
  BATCH_TASKS,
  buildBatchTaskRequest,
  createBatchLimitDrafts,
  type BatchLimitDraft,
  type BatchTaskKey,
} from './batchTasks';
import {
  batchLimitInsertionText,
  batchLimitLabel,
  isBatchLimitText,
  parseBatchLimit,
} from './batchLimits';

interface ManagePageProps {
  papers: Paper[];
  notify: (message: string) => void;
  reloadPapers: () => Promise<void>;
  removeReadingQueue: (ids: readonly string[]) => void;
  openPaper: (id: string) => void;
}

type EditField =
  | 'title'
  | 'title_zh'
  | 'venue'
  | 'year'
  | 'type'
  | 'topic'
  | 'url'
  | 'pdf_url'
  | 'arxiv_id'
  | 'doi'
  | 'tldr'
  | 'contribution';

const EDIT_GROUPS: Array<{
  id: string;
  label: string;
  wide?: boolean;
  fields: Array<{ key: EditField; label: string; wide?: boolean }>;
}> = [
  {
    id: 'basic',
    label: '基本信息',
    fields: [
      { key: 'title', label: '英文题名', wide: true },
      { key: 'title_zh', label: '中文题名', wide: true },
      { key: 'venue', label: '会议/期刊' },
      { key: 'year', label: '年份' },
      { key: 'type', label: '研究类型' },
      { key: 'topic', label: '主题' },
    ],
  },
  {
    id: 'identifiers',
    label: '标识与链接',
    fields: [
      { key: 'arxiv_id', label: 'arXiv ID' },
      { key: 'doi', label: 'DOI' },
      { key: 'url', label: '来源链接', wide: true },
      { key: 'pdf_url', label: 'PDF 链接', wide: true },
    ],
  },
  {
    id: 'summary',
    label: '摘要信息',
    wide: true,
    fields: [
      { key: 'tldr', label: 'TL;DR 摘要', wide: true },
      { key: 'contribution', label: '核心贡献', wide: true },
    ],
  },
];

const EDIT_LABELS = EDIT_GROUPS.flatMap((group) => group.fields);

const MANAGE_SECTIONS = [
  { id: 'manage-add', label: '单篇新增' },
  { id: 'manage-edit', label: '单篇编辑' },
  { id: 'manage-tools', label: '批量维护' },
  { id: 'manage-delete', label: '危险操作' },
] as const;

function PaperFields({
  idPrefix,
  accessiblePrefix,
  values,
  titleRequired,
  onChange,
}: {
  idPrefix: string;
  accessiblePrefix: string;
  values: Partial<Record<EditField, string>>;
  titleRequired?: boolean;
  onChange: (key: EditField, value: string) => void;
}) {
  return (
    <div className="manage__field-groups">
      {EDIT_GROUPS.map((group) => (
        <fieldset
          key={group.id}
          className={`manage__field-group${group.wide ? ' manage__field-group--wide' : ''}`}
        >
          <legend>{group.label}</legend>
          <div className="manage__form">
            {group.fields.map((field) => {
              const fieldId = `${idPrefix}-${field.key}`;
              const isLongText = field.key === 'tldr' || field.key === 'contribution';
              const required = titleRequired && field.key === 'title';
              return (
                <label
                  key={field.key}
                  className={field.wide ? 'manage__field manage__field--wide' : 'manage__field'}
                  htmlFor={fieldId}
                >
                  <span>
                    {field.label}
                    {required && <em className="manage__required"> *</em>}
                  </span>
                  {isLongText ? (
                    <textarea
                      className="input"
                      id={fieldId}
                      aria-label={`${accessiblePrefix}${field.label}`}
                      aria-required={required || undefined}
                      value={values[field.key] ?? ''}
                      onChange={(event) => onChange(field.key, event.target.value)}
                    />
                  ) : (
                    <input
                      className="input"
                      id={fieldId}
                      aria-label={`${accessiblePrefix}${field.label}`}
                      aria-required={required || undefined}
                      value={values[field.key] ?? ''}
                      onChange={(event) => onChange(field.key, event.target.value)}
                    />
                  )}
                </label>
              );
            })}
          </div>
        </fieldset>
      ))}
    </div>
  );
}

function BatchLimitControl({
  id,
  accessibleName,
  draft,
  disabled,
  onChange,
}: {
  id: string;
  accessibleName: string;
  draft: BatchLimitDraft;
  disabled: boolean;
  onChange: (next: BatchLimitDraft) => void;
}) {
  const result = parseBatchLimit(draft.value, draft.inputInvalid);
  const descriptionId = `${id}-description`;
  const rejectInvalidInsertion = (text: string) => {
    if (isBatchLimitText(text)) return false;
    onChange({ ...draft, inputInvalid: true });
    return true;
  };

  return (
    <label className="manage__limit-control">
      <span>本次处理篇数</span>
      <input
        className="input"
        id={id}
        type="number"
        min="0"
        step="1"
        inputMode="numeric"
        aria-label={accessibleName}
        aria-describedby={descriptionId}
        aria-invalid={result.valid ? undefined : true}
        value={draft.value}
        disabled={disabled}
        onKeyDown={(event) => {
          if (
            !event.ctrlKey &&
            !event.metaKey &&
            !event.altKey &&
            event.key.length === 1 &&
            rejectInvalidInsertion(event.key)
          ) {
            event.preventDefault();
          }
        }}
        onBeforeInput={(event) => {
          const syntheticData = (event as typeof event & { data?: unknown }).data;
          const nativeData = 'data' in event.nativeEvent
            ? (event.nativeEvent as InputEvent).data
            : undefined;
          const text = batchLimitInsertionText(syntheticData, nativeData);
          if (text !== null && rejectInvalidInsertion(text)) event.preventDefault();
        }}
        onPaste={(event) => {
          if (rejectInvalidInsertion(event.clipboardData.getData('text'))) event.preventDefault();
        }}
        onDrop={(event) => {
          if (rejectInvalidInsertion(event.dataTransfer.getData('text'))) event.preventDefault();
        }}
        onChange={(event) =>
          onChange({
            value: event.currentTarget.value,
            inputInvalid: event.currentTarget.validity.badInput,
          })
        }
      />
      <small id={descriptionId} aria-live="polite">
        {result.valid ? '留空或 0 = 全部' : result.error}
      </small>
    </label>
  );
}

function formatLastRun(run: BatchRun | null | undefined): string {
  if (!run) return '上次运行：暂无记录';
  const normalized = run.finishedAt.includes('T') ? run.finishedAt : run.finishedAt.replace(' ', 'T');
  /* SQLite datetime('now') 保存 UTC；无时区后缀时显式按 UTC 解析，避免显示慢 8 小时。 */
  const timestamp = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalized) ? normalized : `${normalized}Z`;
  const parsed = new Date(timestamp);
  const time = Number.isNaN(parsed.getTime())
    ? run.finishedAt
    : parsed.toLocaleString('zh-CN', { hour12: false });
  return `上次运行：${time} · 成 ${run.done} / 败 ${run.failed} / 跳 ${run.skipped}`;
}

export function ManagePage({
  papers,
  notify,
  reloadPapers,
  removeReadingQueue,
  openPaper,
}: ManagePageProps) {
  /* ── 新增论文 ── */
  const [draft, setDraft] = useState<Partial<Record<EditField, string>>>({});
  const [adding, setAdding] = useState(false);

  const addPaper = async () => {
    if (!draft.title?.trim()) {
      notify('英文题名为必填项');
      return;
    }
    setAdding(true);
    try {
      const result = await libraryApi.addPaper(draft);
      if (result.ok) {
        notify(`已入库（id: ${result.id}）`);
        setDraft({});
        await reloadPapers();
      } else {
        notify(`新增失败：${result.error}`);
      }
    } catch (error) {
      notify(`新增失败：${error instanceof Error ? error.message : error}`);
    } finally {
      setAdding(false);
    }
  };

  /* ── 编辑现有论文 ── */
  const [editQuery, setEditQuery] = useState('');
  const [editTarget, setEditTarget] = useState<Paper | null>(null);
  const [editDraft, setEditDraft] = useState<Partial<Record<EditField, string>>>({});
  const [savingEdit, setSavingEdit] = useState(false);

  const editMatches = useMemo(() => {
    const q = editQuery.trim().toLowerCase();
    if (!q) return papers.slice(0, 6);
    return papers
      .filter(
        (paper) =>
          (paper.title ?? '').toLowerCase().includes(q) ||
          (paper.title_zh ?? '').includes(editQuery.trim()) ||
          (paper.id ?? '').toLowerCase().includes(q),
      )
      .slice(0, 6);
  }, [papers, editQuery]);

  const pickEditTarget = (paper: Paper) => {
    setEditTarget(paper);
    setEditDraft(
      Object.fromEntries(EDIT_LABELS.map(({ key }) => [key, String((paper[key] as string) ?? '')])),
    );
  };

  const saveEdit = async () => {
    if (!editTarget) return;
    const payload = Object.fromEntries(
      EDIT_LABELS.filter(({ key }) => editDraft[key] !== undefined).map(({ key }) => [
        key,
        editDraft[key] ?? '',
      ]),
    );
    setSavingEdit(true);
    try {
      const result = await libraryApi.updatePaper(editTarget.id, payload);
      if (result.ok) {
        notify(`已保存（${result.changes ?? 0} 个字段更新）`);
        await reloadPapers();
        setEditTarget(null);
      } else {
        notify(`保存失败：${result.error}`);
      }
    } catch (error) {
      notify(`保存失败：${error instanceof Error ? error.message : error}`);
    } finally {
      setSavingEdit(false);
    }
  };

  /* ── 批量工具（全部为 NDJSON 流式任务） ── */
  const titleStream = useStream();
  const venueStream = useStream();
  const scanStream = useStream();
  const downloadStream = useStream();
  const batchStream = useStream();
  const ocrBatchStream = useStream();
  const embedStream = useStream();
  const enrichStream = useStream();

  const [titlePending, setTitlePending] = useState<number | null>(null);
  const [batchStatus, setBatchStatus] = useState<{
    pending?: number;
    withPdf?: number;
    running?: boolean;
    lastRun?: BatchRun | null;
  } | null>(null);
  const [ocrBatchStatus, setOcrBatchStatus] = useState<{
    total?: number;
    hasOcr?: number;
    pending?: number;
    noPdf?: number;
    lastRun?: BatchRun | null;
  } | null>(null);
  const [scanDir, setScanDir] = useState('');
  const [scanFiles, setScanFiles] = useState<Array<{ path: string; size: number }>>([]);
  const [scanPicked, setScanPicked] = useState<ReadonlySet<string>>(new Set());
  const [duplicatePairs, setDuplicatePairs] = useState<DuplicatePair[] | null>(null);
  const [duplicateLoading, setDuplicateLoading] = useState(false);
  const [duplicateError, setDuplicateError] = useState('');
  const [enrichStatus, setEnrichStatus] = useState<EnrichStatus | null>(null);
  const [batchLimits, setBatchLimits] = useState(() => createBatchLimitDrafts());
  const updateBatchLimit = (task: BatchTaskKey, draft: BatchLimitDraft) => {
    setBatchLimits((current) => ({ ...current, [task]: draft }));
  };
  const titleLimit = batchLimits.titleTranslations;
  const downloadLimit = batchLimits.pdfDownloads;
  const explainLimit = batchLimits.explanations;
  const ocrLimit = batchLimits.ocrMarkdown;
  const enrichLimit = batchLimits.metadataEnrichment;

  const downloadPendingIds = useMemo(
    () => papers.filter((paper) => !paper.hasPdf && paper.pdf_url).map((paper) => paper.id),
    [papers],
  );
  const titleRequest = buildBatchTaskRequest('titleTranslations', titleLimit.value, titleLimit.inputInvalid);
  const downloadRequest = buildBatchTaskRequest(
    'pdfDownloads',
    downloadLimit.value,
    downloadLimit.inputInvalid,
    downloadPendingIds,
  );
  const explainRequest = buildBatchTaskRequest('explanations', explainLimit.value, explainLimit.inputInvalid);
  const ocrRequest = buildBatchTaskRequest('ocrMarkdown', ocrLimit.value, ocrLimit.inputInvalid);
  const enrichRequest = buildBatchTaskRequest('metadataEnrichment', enrichLimit.value, enrichLimit.inputInvalid);

  const refreshTitleStatus = () => {
    artifactApi
      .titleTranslationStatus()
      .then((status) => setTitlePending(status.pending))
      .catch(() => setTitlePending(null));
  };
  const refreshBatchStatus = () => {
    artifactApi
      .explainBatchStatus()
      .then((status) => setBatchStatus(status as typeof batchStatus))
      .catch(() => setBatchStatus(null));
  };
  const refreshOcrBatchStatus = () => {
    artifactApi
      .ocrBatchStats()
      .then((stats) => setOcrBatchStatus(stats.ok ? stats : null))
      .catch(() => setOcrBatchStatus(null));
  };
  const refreshEnrichStatus = () => {
    maintenanceApi
      .enrichStatus()
      .then((status) => setEnrichStatus(status.ok ? status : null))
      .catch(() => setEnrichStatus(null));
  };
  useEffect(() => {
    refreshTitleStatus();
    refreshBatchStatus();
    refreshOcrBatchStatus();
    refreshEnrichStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const runTitleTranslations = async () => {
    const request = titleRequest;
    if (!request.valid) {
      notify(`标题翻译处理篇数：${request.error}`);
      return;
    }
    const anchor = titleStream.anchorRef.current + 1;
    titleStream.begin();
    try {
      await artifactApi.runTitleTranslations(request.request, (event) =>
        titleStream.accept(anchor, event),
      );
      await reloadPapers();
      refreshTitleStatus();
      notify('标题翻译补齐完成');
    } catch (error) {
      titleStream.fail(anchor, error);
    }
  };

  const runNormVenues = async () => {
    const anchor = venueStream.anchorRef.current + 1;
    venueStream.begin();
    try {
      await acquireApi.normVenues((event) => venueStream.accept(anchor, event));
      await reloadPapers();
      notify('会议名规范完成');
    } catch (error) {
      venueStream.fail(anchor, error);
    }
  };

  const runScan = async () => {
    if (!scanDir.trim()) {
      notify('请输入本机 PDF 文件夹路径');
      return;
    }
    try {
      const result = await acquireApi.scanPdfs(scanDir.trim());
      const files = (result.files as Array<{ path: string; size: number }>) ?? [];
      setScanFiles(files);
      setScanPicked(new Set(files.map((file) => file.path)));
      notify(`扫描到 ${files.length} 个 PDF`);
    } catch (error) {
      notify(`扫描失败：${error instanceof Error ? error.message : error}`);
    }
  };

  const runImport = async () => {
    const paths = scanFiles.filter((file) => scanPicked.has(file.path)).map((file) => file.path);
    if (paths.length === 0) return;
    const anchor = scanStream.anchorRef.current + 1;
    scanStream.begin();
    try {
      await acquireApi.importPdfs({ paths }, (event) => scanStream.accept(anchor, event));
      await reloadPapers();
      notify('本地 PDF 导入完成');
      setScanFiles([]);
    } catch (error) {
      scanStream.fail(anchor, error);
    }
  };

  const runDownload = async () => {
    const request = downloadRequest;
    if (!request.valid) {
      notify(`PDF 补下载篇数：${request.error}`);
      return;
    }
    const anchor = downloadStream.anchorRef.current + 1;
    downloadStream.begin();
    try {
      await acquireApi.downloadPdfs(
        request.request,
        (event) => downloadStream.accept(anchor, event),
      );
      await reloadPapers();
      notify('PDF 补下载完成');
    } catch (error) {
      downloadStream.fail(anchor, error);
    }
  };

  const runExplainBatch = async () => {
    const request = explainRequest;
    if (!request.valid) {
      notify(`批量讲解篇数：${request.error}`);
      return;
    }
    const anchor = batchStream.anchorRef.current + 1;
    batchStream.begin();
    try {
      const onEvent = (event: Parameters<typeof batchStream.accept>[1]) =>
        batchStream.accept(anchor, event);
      if (explainLimit.value === '3' && !explainLimit.inputInvalid) {
        await artifactApi.explainBatch({ limit: 3 }, onEvent);
      } else {
        await artifactApi.explainBatch(request.request, onEvent);
      }
      refreshBatchStatus();
      notify('批量讲解完成');
    } catch (error) {
      batchStream.fail(anchor, error);
    }
  };

  const runOcrBatch = async () => {
    const request = ocrRequest;
    if (!request.valid) {
      notify(`PDF 转 Markdown 篇数：${request.error}`);
      return;
    }
    const anchor = ocrBatchStream.anchorRef.current + 1;
    ocrBatchStream.begin();
    try {
      const onEvent = (event: Parameters<typeof ocrBatchStream.accept>[1]) =>
        ocrBatchStream.accept(anchor, event);
      if (ocrLimit.value === '3' && !ocrLimit.inputInvalid) {
        await artifactApi.ocrBatch({ limit: 3 }, onEvent);
      } else {
        await artifactApi.ocrBatch(request.request, onEvent);
      }
      refreshOcrBatchStatus();
      notify('批量 PDF → Markdown 完成（已落库并写入 OCR 目录）');
    } catch (error) {
      ocrBatchStream.fail(anchor, error);
    }
  };

  const runDuplicateScan = async () => {
    setDuplicateLoading(true);
    setDuplicateError('');
    try {
      const result = await maintenanceApi.duplicateScan();
      if (!result.ok) throw new Error(result.error || '扫描失败');
      setDuplicatePairs(result.pairs ?? []);
      notify(`疑似重复扫描完成：发现 ${result.count ?? result.pairs?.length ?? 0} 对`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setDuplicatePairs(null);
      setDuplicateError(message);
      notify(`疑似重复扫描失败：${message}`);
    } finally {
      setDuplicateLoading(false);
    }
  };

  const runEnrich = async () => {
    const request = enrichRequest;
    if (!request.valid) {
      notify(`元数据补全篇数：${request.error}`);
      return;
    }
    const anchor = enrichStream.anchorRef.current + 1;
    enrichStream.begin();
    try {
      let sawTerminal = false;
      let terminalError = '';
      await maintenanceApi.enrich(
        request.request,
        (event) => {
          enrichStream.accept(anchor, event);
          if (event.type === 'done' || event.type === 'result') {
            sawTerminal = true;
            if (event.ok === false) terminalError = String(event.error || '元数据补全失败');
          }
        },
      );
      if (!sawTerminal) throw new Error('元数据补全未返回终态事件');
      if (terminalError) throw new Error(terminalError);
      await reloadPapers();
      refreshEnrichStatus();
      notify('元数据补全完成');
    } catch (error) {
      enrichStream.fail(anchor, error);
      notify(`元数据补全失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const runEmbed = async (scope: 'all' | 'missing') => {
    const anchor = embedStream.anchorRef.current + 1;
    embedStream.begin();
    try {
      await acquireApi.embed(scope, (event) => embedStream.accept(anchor, event));
      notify(scope === 'all' ? '全量语义索引重建完成' : '缺失索引补齐完成');
    } catch (error) {
      embedStream.fail(anchor, error);
    }
  };

  /* ── 删除（单篇 + 条件批量，均调 POST /api/delete 逐篇执行） ── */
  const [deleteTargets, setDeleteTargets] = useState<Paper[] | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteProgress, setDeleteProgress] = useState('');
  const [delStatus, setDelStatus] = useState<'all' | StudyStatus>('all');
  const [delSource, setDelSource] = useState('all');
  const [delTopic, setDelTopic] = useState('all');
  const [delYear, setDelYear] = useState('all');
  const [delPdf, setDelPdf] = useState<'all' | 'with' | 'without'>('all');
  const [delFavorite, setDelFavorite] = useState<'all' | 'fav' | 'unfav'>('all');

  const sourceOptions = useMemo(
    () => Array.from(new Set(papers.map((paper) => paper.source).filter(Boolean))),
    [papers],
  );
  const topicOptions = useMemo(
    () => Array.from(new Set(papers.map((paper) => paper.topic).filter(Boolean))),
    [papers],
  );
  const yearOptions = useMemo(
    () =>
      Array.from(new Set(papers.map((paper) => paper.year).filter(Boolean))).sort(
        (a, b) => Number(b) - Number(a),
      ),
    [papers],
  );

  const deleteMatches = useMemo(
    () =>
      papers.filter(
        (paper) =>
          (delStatus === 'all' || paper.status === delStatus) &&
          (delSource === 'all' || paper.source === delSource) &&
          (delTopic === 'all' || paper.topic === delTopic) &&
          (delYear === 'all' || paper.year === delYear) &&
          (delPdf === 'all' || (delPdf === 'with' ? paper.hasPdf : !paper.hasPdf)) &&
          (delFavorite === 'all' ||
            (delFavorite === 'fav' ? paper.favorite === 1 : paper.favorite !== 1)),
      ),
    [papers, delStatus, delSource, delTopic, delYear, delPdf, delFavorite],
  );

  const executeDelete = async () => {
    if (!deleteTargets || deleteTargets.length === 0) return;
    setDeleting(true);
    let ok = 0;
    let failed = 0;
    const deletedIds: string[] = [];
    for (let i = 0; i < deleteTargets.length; i++) {
      setDeleteProgress(`正在删除 ${i + 1}/${deleteTargets.length}…`);
      try {
        await libraryApi.deletePaper(deleteTargets[i].id);
        deletedIds.push(deleteTargets[i].id);
        ok += 1;
      } catch {
        failed += 1;
      }
    }
    if (deletedIds.length > 0) removeReadingQueue(deletedIds);
    await reloadPapers();
    setDeleting(false);
    setDeleteProgress('');
    setDeleteTargets(null);
    if (editTarget && deleteTargets.some((paper) => paper.id === editTarget.id)) {
      setEditTarget(null);
    }
    notify(failed === 0 ? `已删除 ${ok} 篇论文` : `删除完成：成功 ${ok} 篇，失败 ${failed} 篇`);
  };

  return (
    <div className="page page-enter manage">
      <nav className="manage__section-nav" aria-label="管理页分区">
        {MANAGE_SECTIONS.map((section) => (
          <a key={section.id} href={`#${section.id}`}>
            {section.label}
          </a>
        ))}
      </nav>

      <div className="manage__grid">
        {/* ── 新增论文 ── */}
        <section className="card manage__panel" id="manage-add" aria-labelledby="manage-add-title">
          <header className="insights__panel-head">
            <h2 className="section-title" id="manage-add-title">
              手动新增论文
            </h2>
            <span className="eyebrow">POST /api/paper/add</span>
          </header>
          <PaperFields
            idPrefix="manage-add"
            accessiblePrefix="新增论文"
            values={draft}
            titleRequired
            onChange={(key, value) => setDraft((prev) => ({ ...prev, [key]: value }))}
          />
          <button type="button" className="btn btn--primary" onClick={() => void addPaper()} disabled={adding}>
            <PlusIcon size={14} />
            {adding ? '入库中…' : '入库'}
          </button>
        </section>

        {/* ── 编辑论文 ── */}
        <section className="card manage__panel" id="manage-edit" aria-labelledby="manage-edit-title">
          <header className="insights__panel-head">
            <h2 className="section-title" id="manage-edit-title">
              编辑论文字段
            </h2>
            <span className="eyebrow">POST /api/paper/update</span>
          </header>
          <label className="library__search input">
            <SearchIcon size={15} />
            <input
              placeholder="检索要编辑的论文…"
              aria-label="检索要编辑的论文"
              value={editQuery}
              onChange={(event) => setEditQuery(event.target.value)}
            />
          </label>
          <p className="manage__picks-meta">
            {editQuery.trim()
              ? `命中 ${editMatches.length} 篇（最多显示 6 篇）· 点标题展开字段编辑`
              : '显示最近 6 篇 · 点标题展开字段编辑，或输入关键词检索'}
          </p>
          {editMatches.length > 0 ? (
            <ul className="manage__picks">
              {editMatches.map((paper) => (
                <li key={paper.id} className="manage__pick-row">
                  <button type="button" className={editTarget?.id === paper.id ? 'is-active' : ''} onClick={() => pickEditTarget(paper)}>
                    {paper.title_zh || paper.title}
                  </button>
                  <button
                    type="button"
                    className="btn btn--danger-ghost btn--sm"
                    aria-label={`删除 ${paper.title_zh || paper.title}`}
                    onClick={() => setDeleteTargets([paper])}
                  >
                    删除
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="manage__picks-empty">未找到匹配的论文，请调整检索词。</p>
          )}
          {editTarget ? (
            <PaperFields
              idPrefix="manage-edit"
              accessiblePrefix="编辑论文"
              values={editDraft}
              onChange={(key, value) => setEditDraft((prev) => ({ ...prev, [key]: value }))}
            />
          ) : (
            <div className="manage__edit-empty" role="status">
              <strong>选择一篇论文后编辑</strong>
              <span>从上方最近论文或检索结果中选择，完整字段将在此处展开。</span>
            </div>
          )}
          {editTarget && (
            <div className="deep__actions">
              <button type="button" className="btn btn--primary" onClick={() => void saveEdit()} disabled={savingEdit}>
                保存修改
              </button>
              <button type="button" className="btn" onClick={() => openPaper(editTarget.id)}>
                在文献库查看
              </button>
              <button type="button" className="btn btn--ghost" onClick={() => setEditTarget(null)}>
                取消
              </button>
            </div>
          )}
        </section>

        {/* ── 批量工具 ── */}
        <section
          className="card manage__panel manage__panel--wide manage__panel--tools"
          id="manage-tools"
          aria-labelledby="manage-tools-title"
        >
          <header className="insights__panel-head">
            <h2 className="section-title" id="manage-tools-title">
              批量维护工具
            </h2>
            <span className="eyebrow">NDJSON 流式任务</span>
          </header>

          <div className="manage__tools">
            <article
              className="manage__tool"
              aria-labelledby="manage-tool-title-translations"
              aria-busy={titleStream.state.running}
            >
              <h3 id="manage-tool-title-translations">标题中文翻译补齐</h3>
              <p className="deep__fact">
                待翻译 {titlePending ?? '—'} 篇 <code className="manage__endpoint">GET /api/title-translations</code>
              </p>
              <BatchLimitControl
                id={BATCH_TASKS.titleTranslations.inputId}
                accessibleName={BATCH_TASKS.titleTranslations.accessibleName}
                draft={titleLimit}
                disabled={titleStream.state.running}
                onChange={(draft) => updateBatchLimit('titleTranslations', draft)}
              />
              <button
                type="button"
                className="btn btn--primary btn--sm"
                onClick={() => void runTitleTranslations()}
                disabled={
                  titleStream.state.running ||
                  titlePending === 0 ||
                  !titleRequest.valid
                }
              >
                {titlePending === 0
                  ? '无需补齐'
                  : `补齐（${batchLimitLabel(titleLimit.value, titleLimit.inputInvalid)}）`}
              </button>
              <StreamConsole state={titleStream.state} />
            </article>

            <article
              className="manage__tool"
              aria-labelledby="manage-tool-normalize-venues"
              aria-busy={venueStream.state.running}
            >
              <h3 id="manage-tool-normalize-venues">会议名规范</h3>
              <p className="deep__fact">
                统一 venue 缩写为权威名称 <code className="manage__endpoint">POST /api/norm-venues</code>
              </p>
              <button type="button" className="btn btn--primary btn--sm" onClick={() => void runNormVenues()} disabled={venueStream.state.running}>
                执行规范
              </button>
              <StreamConsole state={venueStream.state} />
            </article>

            <article
              className="manage__tool"
              aria-labelledby="manage-tool-import-pdfs"
              aria-busy={scanStream.state.running}
            >
              <h3 id="manage-tool-import-pdfs">本地 PDF 导入</h3>
              <div className="reviews__start-row">
                <input
                  className="input"
                  placeholder="本机文件夹绝对路径…"
                  aria-label="PDF 文件夹"
                  value={scanDir}
                  onChange={(event) => setScanDir(event.target.value)}
                />
                <button type="button" className="btn btn--sm" onClick={() => void runScan()}>
                  扫描
                </button>
              </div>
              {scanFiles.length > 0 && (
                <ul className="manage__scanlist">
                  {scanFiles.map((file) => (
                    <li key={file.path}>
                      <label>
                        <input
                          type="checkbox"
                          checked={scanPicked.has(file.path)}
                          onChange={() =>
                            setScanPicked((prev) => {
                              const next = new Set(prev);
                              if (next.has(file.path)) next.delete(file.path);
                              else next.add(file.path);
                              return next;
                            })
                          }
                        />
                        <span title={file.path}>{file.path.split(/[\\/]/).pop()}</span>
                        <small>{(file.size / 1024 / 1024).toFixed(1)} MB</small>
                      </label>
                    </li>
                  ))}
                </ul>
              )}
              <button
                type="button"
                className="btn btn--primary btn--sm"
                onClick={() => void runImport()}
                disabled={scanPicked.size === 0 || scanStream.state.running}
              >
                导入所选（{scanPicked.size}）
              </button>
              <StreamConsole state={scanStream.state} />
            </article>

            <article
              className="manage__tool"
              aria-labelledby="manage-tool-download-pdfs"
              aria-busy={downloadStream.state.running}
            >
              <h3 id="manage-tool-download-pdfs">PDF 批量补下载</h3>
              <p className="deep__fact">
                为有 PDF 链接但缺本地文件的论文补齐 <code className="manage__endpoint">POST /api/download-pdfs</code>
              </p>
              <BatchLimitControl
                id={BATCH_TASKS.pdfDownloads.inputId}
                accessibleName={BATCH_TASKS.pdfDownloads.accessibleName}
                draft={downloadLimit}
                disabled={downloadStream.state.running}
                onChange={(draft) => updateBatchLimit('pdfDownloads', draft)}
              />
              <button
                type="button"
                className="btn btn--primary btn--sm"
                onClick={() => void runDownload()}
                disabled={
                  downloadStream.state.running ||
                  downloadPendingIds.length === 0 ||
                  !downloadRequest.valid
                }
              >
                {downloadPendingIds.length === 0
                  ? '无需补下载'
                  : `补下载（${batchLimitLabel(downloadLimit.value, downloadLimit.inputInvalid)}）`}
              </button>
              <StreamConsole state={downloadStream.state} />
            </article>

            <article
              className="manage__tool"
              aria-labelledby="manage-tool-explain-batch"
              aria-busy={batchStream.state.running}
            >
              <h3 id="manage-tool-explain-batch">批量生成讲解</h3>
              <p className="deep__fact">
                待生成 {batchStatus?.pending ?? '—'} 篇 · 有 PDF {batchStatus?.withPdf ?? '—'} 篇{' '}
                <code className="manage__endpoint">GET /api/explain-batch</code>
              </p>
              <p className="manage__last-run">{formatLastRun(batchStatus?.lastRun)}</p>
              <BatchLimitControl
                id={BATCH_TASKS.explanations.inputId}
                accessibleName={BATCH_TASKS.explanations.accessibleName}
                draft={explainLimit}
                disabled={batchStream.state.running}
                onChange={(draft) => updateBatchLimit('explanations', draft)}
              />
              <button
                type="button"
                className="btn btn--primary btn--sm"
                onClick={() => void runExplainBatch()}
                disabled={
                  batchStream.state.running ||
                  batchStatus?.pending === 0 ||
                  !explainRequest.valid
                }
              >
                {batchStream.state.running
                  ? '讲解进行中…'
                  : batchStatus?.pending === 0
                    ? '无需讲解'
                    : `批量讲解（${batchLimitLabel(explainLimit.value, explainLimit.inputInvalid)}）`}
              </button>
              <StreamConsole state={batchStream.state} />
            </article>

            <article
              className="manage__tool"
              aria-labelledby="manage-tool-ocr-batch"
              aria-busy={ocrBatchStream.state.running}
            >
              <h3 id="manage-tool-ocr-batch">批量 PDF → Markdown</h3>
              <p className="deep__fact">
                待转换 {ocrBatchStatus?.pending ?? '—'} 篇 · 已有 OCR {ocrBatchStatus?.hasOcr ?? '—'} 篇 · 缺少 PDF{' '}
                {ocrBatchStatus?.noPdf ?? '—'} 篇 <code className="manage__endpoint">GET /api/ocr-md-batch</code>
              </p>
              <p className="manage__last-run">{formatLastRun(ocrBatchStatus?.lastRun)}</p>
              <BatchLimitControl
                id={BATCH_TASKS.ocrMarkdown.inputId}
                accessibleName={BATCH_TASKS.ocrMarkdown.accessibleName}
                draft={ocrLimit}
                disabled={ocrBatchStream.state.running}
                onChange={(draft) => updateBatchLimit('ocrMarkdown', draft)}
              />
              <button
                type="button"
                className="btn btn--primary btn--sm"
                onClick={() => void runOcrBatch()}
                disabled={
                  ocrBatchStream.state.running ||
                  (ocrBatchStatus ? ocrBatchStatus.pending === 0 : false) ||
                  !ocrRequest.valid
                }
              >
                {ocrBatchStream.state.running
                  ? '转换进行中…'
                  : ocrBatchStatus && ocrBatchStatus.pending === 0
                    ? '无需转换（已全部有 OCR 或无 PDF）'
                    : `批量转换（${batchLimitLabel(ocrLimit.value, ocrLimit.inputInvalid)}）`}
              </button>
              <StreamConsole state={ocrBatchStream.state} />
            </article>

            <article
              className="manage__tool"
              aria-labelledby="manage-tool-duplicate-scan"
              aria-busy={duplicateLoading}
            >
              <h3 id="manage-tool-duplicate-scan">疑似重复扫描</h3>
              <p className="deep__fact" aria-live="polite">
                {duplicatePairs === null ? '尚未扫描' : `发现 ${duplicatePairs.length} 对疑似重复论文`}{' '}
                <code className="manage__endpoint">GET /api/dup-scan</code>
              </p>
              {duplicateError && (
                <p className="manage__tool-error" role="alert">
                  扫描失败：{duplicateError}
                </p>
              )}
              {duplicatePairs && duplicatePairs.length > 0 && (
                <ol className="manage__pairs" aria-label="疑似重复论文对">
                  {duplicatePairs.map((pair) => (
                    <li key={`${pair.left.id}:${pair.right.id}`}>
                      <div>
                        <strong title={pair.left.title || pair.left.id}>{pair.left.title || pair.left.id}</strong>
                        <small>{[pair.left.venue, pair.left.year].filter(Boolean).join(' · ') || '元数据缺失'}</small>
                      </div>
                      <span className="manage__pair-score" aria-label={`相似度 ${Math.round(pair.similarity * 100)}%`}>
                        ≈ {Math.round(pair.similarity * 100)}%
                      </span>
                      <div>
                        <strong title={pair.right.title || pair.right.id}>{pair.right.title || pair.right.id}</strong>
                        <small>{[pair.right.venue, pair.right.year].filter(Boolean).join(' · ') || '元数据缺失'}</small>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
              <button
                type="button"
                className="btn btn--primary btn--sm"
                onClick={() => void runDuplicateScan()}
                disabled={duplicateLoading}
              >
                {duplicateLoading ? '扫描中…' : '扫描'}
              </button>
            </article>

            <article
              className="manage__tool"
              aria-labelledby="manage-tool-enrichment"
              aria-busy={enrichStream.state.running}
            >
              <h3 id="manage-tool-enrichment">元数据补全</h3>
              <p className="deep__fact">
                缺 year/venue {enrichStatus?.missingMetadata ?? '—'} 篇 · 已录作者{' '}
                {enrichStatus?.withAuthors ?? '—'} 篇 · 待补作者 {enrichStatus?.missingAuthors ?? '—'} 篇{' '}
                <code className="manage__endpoint">POST /api/enrich</code>
              </p>
              <BatchLimitControl
                id={BATCH_TASKS.metadataEnrichment.inputId}
                accessibleName={BATCH_TASKS.metadataEnrichment.accessibleName}
                draft={enrichLimit}
                disabled={enrichStream.state.running}
                onChange={(draft) => updateBatchLimit('metadataEnrichment', draft)}
              />
              <button
                type="button"
                className="btn btn--primary btn--sm"
                onClick={() => void runEnrich()}
                disabled={
                  enrichStream.state.running ||
                  (enrichStatus ? enrichStatus.pending === 0 : false) ||
                  !enrichRequest.valid
                }
              >
                {enrichStream.state.running
                  ? '补全进行中…'
                  : enrichStatus && enrichStatus.pending === 0
                    ? '无需补全'
                    : `补全（${batchLimitLabel(enrichLimit.value, enrichLimit.inputInvalid)}）`}
              </button>
              <StreamConsole state={enrichStream.state} />
            </article>

            <article
              className="manage__tool"
              aria-labelledby="manage-tool-embeddings"
              aria-busy={embedStream.state.running}
            >
              <h3 id="manage-tool-embeddings">语义索引维护</h3>
              <p className="deep__fact">
                重建本地嵌入索引以支持语义检索 <code className="manage__endpoint">POST /api/embed</code>
              </p>
              <div className="deep__actions">
                <button type="button" className="btn btn--sm" onClick={() => void runEmbed('missing')} disabled={embedStream.state.running}>
                  补齐缺失
                </button>
                <button type="button" className="btn btn--sm" onClick={() => void runEmbed('all')} disabled={embedStream.state.running}>
                  全量重建
                </button>
              </div>
              <StreamConsole state={embedStream.state} />
            </article>
          </div>
        </section>

        {/* ── 条件批量删除 ── */}
        <section
          className="card manage__panel manage__panel--wide manage__panel--danger"
          id="manage-delete"
          aria-labelledby="manage-delete-title"
          aria-describedby="manage-delete-warning"
        >
          <header className="insights__panel-head">
            <h2 className="section-title" id="manage-delete-title">
              条件批量删除
            </h2>
            <span className="eyebrow">POST /api/delete · 危险操作</span>
          </header>
          <p className="manage__delete-hint" id="manage-delete-warning">
            组合以下条件圈定待删论文，删除前会弹出确认框逐篇核对；删除不可撤销。
          </p>
          <div className="manage__form">
            <label className="manage__field">
              <span>学习状态</span>
              <select
                className="input"
                aria-label="删除条件：学习状态"
                value={delStatus}
                onChange={(event) => setDelStatus(event.target.value as typeof delStatus)}
              >
                <option value="all">全部</option>
                <option value="未开始">未开始</option>
                <option value="学习中">学习中</option>
                <option value="已理解">已理解</option>
              </select>
            </label>
            <label className="manage__field">
              <span>采集来源</span>
              <select
                className="input"
                aria-label="删除条件：来源"
                value={delSource}
                onChange={(event) => setDelSource(event.target.value)}
              >
                <option value="all">全部</option>
                {sourceOptions.map((source) => (
                  <option key={source} value={source}>
                    {source}
                  </option>
                ))}
              </select>
            </label>
            <label className="manage__field">
              <span>主题</span>
              <select
                className="input"
                aria-label="删除条件：主题"
                value={delTopic}
                onChange={(event) => setDelTopic(event.target.value)}
              >
                <option value="all">全部</option>
                {topicOptions.map((topic) => (
                  <option key={topic} value={topic}>
                    {topic}
                  </option>
                ))}
              </select>
            </label>
            <label className="manage__field">
              <span>年份</span>
              <select
                className="input"
                aria-label="删除条件：年份"
                value={delYear}
                onChange={(event) => setDelYear(event.target.value)}
              >
                <option value="all">全部</option>
                {yearOptions.map((year) => (
                  <option key={year} value={year}>
                    {year}
                  </option>
                ))}
              </select>
            </label>
            <label className="manage__field">
              <span>本地 PDF</span>
              <select
                className="input"
                aria-label="删除条件：PDF"
                value={delPdf}
                onChange={(event) => setDelPdf(event.target.value as typeof delPdf)}
              >
                <option value="all">全部</option>
                <option value="with">有 PDF</option>
                <option value="without">无 PDF</option>
              </select>
            </label>
            <label className="manage__field">
              <span>收藏状态</span>
              <select
                className="input"
                aria-label="删除条件：收藏"
                value={delFavorite}
                onChange={(event) => setDelFavorite(event.target.value as typeof delFavorite)}
              >
                <option value="all">全部</option>
                <option value="fav">已收藏</option>
                <option value="unfav">未收藏</option>
              </select>
            </label>
          </div>
          <div className="manage__delete-preview" aria-live="polite">
            <p>
              当前条件命中 <strong className="manage__delete-count">{deleteMatches.length}</strong> 篇
              {deleteMatches.length > 0 && (
                <span className="manage__delete-sample">
                  ：{(deleteMatches[0].title_zh || deleteMatches[0].title).slice(0, 30)}
                  {deleteMatches.length > 1 ? ' 等' : ''}
                </span>
              )}
            </p>
            <button
              type="button"
              className="btn btn--danger"
              disabled={deleteMatches.length === 0}
              onClick={() => setDeleteTargets(deleteMatches)}
            >
              批量删除（{deleteMatches.length} 篇）
            </button>
          </div>
        </section>
      </div>

      {deleteTargets && (
        <DeleteConfirmDialog
          papers={deleteTargets}
          running={deleting}
          progress={deleteProgress}
          onCancel={() => setDeleteTargets(null)}
          onConfirm={() => void executeDelete()}
        />
      )}
    </div>
  );
}
