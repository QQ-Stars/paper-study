import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ClipboardEvent as ReactClipboardEvent, DragEvent as ReactDragEvent, KeyboardEvent as ReactKeyboardEvent } from 'react';

import { reproductionApi } from '../api/client';
import type {
  ExperimentRun,
  Paper,
  ReproductionArtifact,
  ReproductionDocument,
  ReproductionNote,
  ReproductionProject,
  ReproductionResult,
  ReproductionResultStatus,
  ReproductionStatus,
} from '../api/types';
import {
  ArchiveIcon,
  ArrowRightIcon,
  CloseIcon,
  DownloadIcon,
  DocumentIcon,
  EditIcon,
  PlusIcon,
  SearchIcon,
  SparkIcon,
  TrashIcon,
} from './Icons';
import { MarkdownView } from './MarkdownView';
import { MarkdownToolbar } from './MarkdownToolbar';
import {
  applyMarkdownCommand,
  commandForMarkdownShortcut,
  continueMarkdownList,
  indentMarkdown,
  normalizedImageFilename,
  supportedImageMimeType,
  type MarkdownCommand,
  type MarkdownEditResult,
  type MarkdownSelection,
} from './markdownEditor';
import { readReproductionListState, writeReproductionListState } from './reproductionState';

import '../styles/reproduction.css';

type EditorMode = 'edit' | 'preview' | 'split';
type SaveState = 'saved' | 'unsaved' | 'saving' | 'failed';

const STATUS_LABELS: Record<ReproductionStatus, string> = {
  planned: '计划中',
  preparing: '准备中',
  running: '运行中',
  completed: '已完成',
  blocked: '受阻',
  archived: '已归档',
};

const DEFAULT_DOCUMENT = `# 复现目标

记录这次复现要验证的研究问题、成功标准和与原论文的对应关系。

## 原论文方法

概述原论文的方法、关键假设与需要复现的核心结果。

## 环境与依赖

记录运行环境、依赖版本、硬件与安装方式。

## 数据集与预处理

记录数据来源、版本、切分和预处理步骤。

## 实验配置

记录模型、超参数、随机种子和配置文件。

## 执行记录

按时间记录每次实验运行、观察和决策。

## 结果对照

对照原论文指标，记录结果、误差和证据。

## 偏差与问题

记录无法复现的部分、排查过程和已知限制。

## 结论与下一步

总结当前结论，并列出下一步实验或资料补充。
`;

const MAX_EDITOR_IMAGE_BYTES = 25 * 1024 * 1024;

const RUN_STATUS_LABELS: Record<ExperimentRun['status'], string> = {
  planned: '计划中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  blocked: '受阻',
};

/* 复现项目没有统一的数值进度字段，使用状态阶段作为轻量视觉进度。 */
const PROJECT_STAGE_PROGRESS: Record<ReproductionStatus, number> = {
  planned: 12,
  preparing: 32,
  running: 64,
  completed: 100,
  blocked: 48,
  archived: 100,
};

const RESULT_STATUS_LABELS: Record<ReproductionResultStatus, string> = {
  reproduced: '已复现',
  partial: '部分复现',
  not_reproduced: '未复现',
  inconsistent: '结果不一致',
};

type RunFormState = {
  name: string;
  environment: string;
  command: string;
  parameters: string;
  dataVersion: string;
  codeRevision: string;
  seed: string;
  status: ExperimentRun['status'];
  metrics: string;
  resultSummary: string;
  startedAt: string;
  finishedAt: string;
  runtimeVersions: string;
  dataset: string;
  preprocessing: string;
  repositoryUrl: string;
  config: string;
  issues: string;
};

const EMPTY_RUN_FORM: RunFormState = {
  name: '', environment: '', command: '', parameters: '', dataVersion: '', codeRevision: '', seed: '',
  status: 'completed', metrics: '', resultSummary: '', startedAt: '', finishedAt: '', runtimeVersions: '',
  dataset: '', preprocessing: '', repositoryUrl: '', config: '', issues: '',
};

function formatDate(value?: string | null): string {
  if (!value) return '尚未记录';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
}

function parseTags(value: string): string[] {
  return [...new Set(value.split(/[\n,，]/).map((tag) => tag.trim()).filter(Boolean))].slice(0, 30);
}

function headings(source: string): Array<{ id: string; label: string; level: number }> {
  const result: Array<{ id: string; label: string; level: number }> = [];
  const seen = new Map<string, number>();
  for (const match of source.matchAll(/^(#{1,3})\s+(.+?)\s*#*$/gm)) {
    const label = match[2].trim();
    const slugBase = label.toLowerCase().replace(/[^\w\u4e00-\u9fff]+/g, '-').replace(/^-|-$/g, '') || 'section';
    const count = (seen.get(slugBase) ?? 0) + 1;
    seen.set(slugBase, count);
    result.push({ id: `${slugBase}-${count}`, label, level: match[1].length });
  }
  return result;
}

function StatusBadge({ status }: { status: ReproductionStatus }) {
  return (
    <span className={`reproduction__status reproduction__status--${status}`}>
      <span className="reproduction__status-dot" aria-hidden="true" />
      {STATUS_LABELS[status]}
    </span>
  );
}

function ReproductionProjectCard({
  project,
  active,
  onSelect,
}: {
  project: ReproductionProject;
  active: boolean;
  onSelect: (id: string) => void;
}) {
  const progress = PROJECT_STAGE_PROGRESS[project.status];
  return (
    <li>
      <button
        type="button"
        className={`reproduction__project${active ? ' reproduction__project--active' : ''}`}
        aria-current={active ? 'true' : undefined}
        aria-label={`打开复现项目 ${project.name}`}
        onClick={() => onSelect(project.id)}
      >
        <span className="reproduction__project-head">
          <span className="reproduction__project-glyph">{project.name.slice(0, 1)}</span>
          <span className="reproduction__project-identity">
            <strong>{project.name}</strong>
            <small>{project.paperTitle || '未关联论文'}{project.paperId ? ` · ${project.paperId}` : ''}</small>
          </span>
        </span>
        <span className="reproduction__project-meta">
          <StatusBadge status={project.status} />
          <em>{formatDate(project.updatedAt)}</em>
        </span>
        <span className="reproduction__project-runs">
          {project.runCount ?? 0} 次运行{project.lastRunSummary ? ` · ${project.lastRunSummary}` : ' · 尚无结果摘要'}
        </span>
        {project.tags.length > 0 && <span className="reproduction__project-tags" aria-label="项目标签">{project.tags.slice(0, 4).map((tag) => <em key={tag}>{tag}</em>)}</span>}
        {(project.hasFailedTask || project.hasUnsavedContent) && <small className="reproduction__project-warning">{project.hasUnsavedContent ? '有未保存内容' : '存在失败任务'}</small>}
        <span className="reproduction__project-progress" aria-label={`项目阶段：${STATUS_LABELS[project.status]}`}>
          <i style={{ width: `${progress}%` }} />
          <em>{STATUS_LABELS[project.status]}</em>
        </span>
      </button>
    </li>
  );
}

function documentFromProject(project: ReproductionProject): ReproductionDocument {
  return project.document ?? {
    id: `${project.id}-document`,
    content: DEFAULT_DOCUMENT,
    revision: 0,
    saveStatus: 'saved',
    updatedAt: project.updatedAt,
  };
}

const DIALOG_FOCUSABLE = [
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[href]',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function useDialogFocus(open: boolean, busy: boolean, onClose: () => void) {
  const dialogRef = useRef<HTMLFormElement>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef(onClose);
  const busyRef = useRef(busy);
  closeRef.current = onClose;
  busyRef.current = busy;

  useEffect(() => {
    if (!open) return;
    triggerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = () => [...dialog.querySelectorAll<HTMLElement>(DIALOG_FOCUSABLE)];
    const initial = dialog.querySelector<HTMLElement>('[autofocus]') ?? focusable()[0];
    initial?.focus();

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (busyRef.current) {
          event.preventDefault();
          event.stopPropagation();
          return;
        }
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== 'Tab') return;
      const elements = focusable();
      if (elements.length === 0) {
        event.preventDefault();
        return;
      }
      const first = elements[0];
      const last = elements[elements.length - 1];
      const activeIndex = elements.indexOf(document.activeElement as HTMLElement);
      if (event.shiftKey && (activeIndex <= 0 || document.activeElement === dialog)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (activeIndex === elements.length - 1 || activeIndex === -1)) {
        event.preventDefault();
        first.focus();
      }
    };

    dialog.addEventListener('keydown', handleKeyDown);
    return () => {
      dialog.removeEventListener('keydown', handleKeyDown);
      const trigger = triggerRef.current;
      if (trigger?.isConnected) trigger.focus();
      triggerRef.current = null;
    };
  }, [open]);

  return dialogRef;
}

interface ReproductionPageProps {
  papers: Paper[];
  notify: (message: string) => void;
  openPaper: (id: string) => void;
  initialPaperId?: string | null;
  initialProjectId?: string | null;
  listOnly?: boolean;
  detailOnly?: boolean;
  onOpenProject?: (id: string) => void;
  onBack?: () => void;
}

function safeLocalStorage(): Storage | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function ReproductionPage({ papers, notify, openPaper, initialPaperId, initialProjectId, listOnly = false, detailOnly = false, onOpenProject, onBack }: ReproductionPageProps) {
  const [projects, setProjects] = useState<ReproductionProject[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<ReproductionProject | null>(null);
  const [runs, setRuns] = useState<ExperimentRun[]>([]);
  const [results, setResults] = useState<ReproductionResult[]>([]);
  const [artifacts, setArtifacts] = useState<ReproductionArtifact[]>([]);
  const [notes, setNotes] = useState<ReproductionNote[]>([]);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState('');
  const savedListState = useMemo(
    () => readReproductionListState(safeLocalStorage()),
    [],
  );
  const [query, setQuery] = useState(savedListState.query);
  const [statusFilter, setStatusFilter] = useState(savedListState.status);
  const [tagFilter, setTagFilter] = useState(savedListState.tag);
  const [sort, setSort] = useState<'updated' | 'created' | 'name'>(savedListState.sort);
  // Editing is the primary task on this page; preview and split remain one-click modes.
  const [mode, setMode] = useState<EditorMode>('edit');
  const [activeHeadingId, setActiveHeadingId] = useState<string | null>(null);
  const [runsExpanded, setRunsExpanded] = useState(false);
  const [draft, setDraft] = useState('');
  const [revision, setRevision] = useState(0);
  const [saveState, setSaveState] = useState<SaveState>('saved');
  const [createOpen, setCreateOpen] = useState(false);
  const [createName, setCreateName] = useState('');
  const [createPaperId, setCreatePaperId] = useState('');
  const [createTags, setCreateTags] = useState('');
  const [createBusy, setCreateBusy] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [runBusy, setRunBusy] = useState(false);
  const [saveConflict, setSaveConflict] = useState(false);
  const [runForm, setRunForm] = useState<RunFormState>(EMPTY_RUN_FORM);
  const [runEditingId, setRunEditingId] = useState<string | null>(null);
  const [resultOpen, setResultOpen] = useState(false);
  const [resultBusy, setResultBusy] = useState(false);
  const [resultForm, setResultForm] = useState({ metricName: '', paperValue: '', reproductionValue: '', difference: '', differencePercent: '', datasetSettings: '', source: '', status: 'not_reproduced' as ReproductionResultStatus, notes: '' });
  const [noteOpen, setNoteOpen] = useState(false);
  const [noteDraft, setNoteDraft] = useState('');
  const [noteBusy, setNoteBusy] = useState(false);
  const [artifactOpen, setArtifactOpen] = useState(false);
  const [artifactFile, setArtifactFile] = useState<File | null>(null);
  const [artifactKind, setArtifactKind] = useState('attachment');
  const [artifactBusy, setArtifactBusy] = useState(false);
  const [editorUploadBusy, setEditorUploadBusy] = useState(false);
  const [editorUploadMessage, setEditorUploadMessage] = useState('');
  const [editorDragActive, setEditorDragActive] = useState(false);
  const [editOpen, setEditOpen] = useState(false);
  const [editName, setEditName] = useState('');
  const [editTags, setEditTags] = useState('');
  const [editBusy, setEditBusy] = useState(false);
  const [artifactsExpanded, setArtifactsExpanded] = useState(false);
  const [notesExpanded, setNotesExpanded] = useState(false);
  const loadedContent = useRef('');
  const initialPaperHandled = useRef(false);
  const dirtyDocumentRef = useRef(false);
  const editorRef = useRef<HTMLDivElement>(null);
  const listScrollRef = useRef<HTMLDivElement>(null);
  const markdownEditorRef = useRef<HTMLTextAreaElement>(null);
  const draftRef = useRef('');
  const editorSelectionRef = useRef<MarkdownSelection>({ start: 0, end: 0 });
  const editorDragDepthRef = useRef(0);
  const selectedIdRef = useRef<string | null>(null);
  const editorProjectRef = useRef<string | null>(null);
  const editorUploadBusyRef = useRef(false);
  const createDialogRef = useDialogFocus(createOpen, createBusy, () => setCreateOpen(false));
  const runDialogRef = useDialogFocus(runOpen, runBusy, () => setRunOpen(false));
  const resultDialogRef = useDialogFocus(resultOpen, resultBusy, () => setResultOpen(false));
  const noteDialogRef = useDialogFocus(noteOpen, noteBusy, () => setNoteOpen(false));
  const artifactDialogRef = useDialogFocus(artifactOpen, artifactBusy, () => setArtifactOpen(false));
  const editDialogRef = useDialogFocus(editOpen, editBusy, () => setEditOpen(false));

  useEffect(() => {
    initialPaperHandled.current = false;
  }, [initialPaperId]);

  useEffect(() => {
    if (!listOnly) return;
    writeReproductionListState(safeLocalStorage(), {
      query,
      status: statusFilter,
      tag: tagFilter,
      sort,
      scrollTop: typeof window === 'undefined' ? 0 : window.scrollY,
    });
  }, [listOnly, query, statusFilter, tagFilter, sort]);

  useEffect(() => {
    if (!listOnly || typeof window === 'undefined') return;
    const restore = () => window.scrollTo({ top: savedListState.scrollTop, behavior: 'auto' });
    const timer = window.setTimeout(restore, 0);
    const handleScroll = () => {
      writeReproductionListState(safeLocalStorage(), {
        query, status: statusFilter, tag: tagFilter, sort, scrollTop: window.scrollY,
      });
    };
    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener('scroll', handleScroll);
    };
  }, [listOnly, query, statusFilter, tagFilter, sort, savedListState.scrollTop]);

  const loadProjects = useCallback(async () => {
    setLoading(true);
    try {
      const response = await reproductionApi.list({
        q: query.trim() || undefined,
        status: statusFilter || undefined,
        tag: tagFilter || undefined,
        sort,
        limit: 100,
      });
      setProjects(response.items ?? []);
      setError('');
      if (listOnly) {
        setSelectedId(null);
      } else if (initialProjectId) {
        setSelectedId(response.items.some((item) => item.id === initialProjectId) ? initialProjectId : null);
      } else if (initialPaperId && !initialPaperHandled.current) {
        initialPaperHandled.current = true;
        const existing = response.items.find((item) => item.paperId === initialPaperId);
        if (existing) {
          setCreateOpen(false);
          setSelectedId(existing.id);
        } else {
          setCreatePaperId(initialPaperId);
          setCreateOpen(true);
          setSelectedId((current) => current && response.items.some((item) => item.id === current) ? current : null);
        }
      } else {
        setSelectedId((current) => {
          if (current && response.items.some((item) => item.id === current)) return current;
          if (current && dirtyDocumentRef.current && !window.confirm('当前复现文档尚未保存，切换筛选会离开编辑内容。继续吗？')) return current;
          return response.items[0]?.id ?? null;
        });
      }
    } catch (reason) {
      setProjects([]);
      setSelectedId(null);
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }, [initialPaperId, initialProjectId, listOnly, query, statusFilter, tagFilter, sort]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadProjects(), 180);
    return () => window.clearTimeout(timer);
  }, [loadProjects]);

  const loadDetail = useCallback(async (id: string) => {
    setDetailLoading(true);
    setSelected((current) => current?.id === id ? current : null);
    try {
      const project = await reproductionApi.get(id);
      setSelected(project);
      const document = documentFromProject(project);
      setDraft(document.content);
      draftRef.current = document.content;
      editorSelectionRef.current = { start: document.content.length, end: document.content.length };
      loadedContent.current = document.content;
      setRevision(document.revision);
      setSaveState('saved');
      const result = await reproductionApi.listRuns(id);
      setRuns(Array.isArray(result) ? result : result.items ?? []);
      setRunsExpanded(false);
      setArtifacts(project.artifacts ?? []);
      setNotes(project.notes ?? []);
      setResults(project.results ?? []);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setSelected(null);
      setRuns([]);
      setRunsExpanded(false);
      setArtifacts([]);
      setNotes([]);
      setResults([]);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (selectedId) void loadDetail(selectedId);
    else {
      setSelected(null);
      setRuns([]);
      setRunsExpanded(false);
      setArtifacts([]);
      setNotes([]);
      setResults([]);
      setArtifactsExpanded(false);
      setNotesExpanded(false);
      setDraft('');
      draftRef.current = '';
      editorSelectionRef.current = { start: 0, end: 0 };
      loadedContent.current = '';
    }
  }, [loadDetail, selectedId]);

  useEffect(() => {
    selectedIdRef.current = selectedId;
    if (editorProjectRef.current !== selectedId) {
      setEditorUploadMessage('');
      setEditorDragActive(false);
      editorDragDepthRef.current = 0;
      editorProjectRef.current = selectedId;
    }
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId || !selected || selected.id !== selectedId || draft === loadedContent.current || saveState === 'saving' || saveState === 'failed') return;
    if (saveState !== 'unsaved') setSaveState('unsaved');
    const timer = window.setTimeout(async () => {
      setSaveState('saving');
      try {
        const saved = await reproductionApi.saveDocument(selectedId, { content: draft, expectedRevision: revision });
        loadedContent.current = draft;
        setRevision(saved.revision);
        setSaveState('saved');
        setSaveConflict(false);
        setSelected((current) => current ? {
          ...current,
          revision: saved.projectRevision ?? current.revision + 1,
          updatedAt: saved.updatedAt,
          document: saved,
        } : current);
      } catch (reason) {
        setSaveState('failed');
        setSaveConflict(reason instanceof Error && (reason.message.includes('409') || reason.message.includes('REPRODUCTION_CONFLICT')));
        setError(reason instanceof Error ? `保存失败：${reason.message}` : `保存失败：${String(reason)}`);
      }
    }, 850);
    return () => window.clearTimeout(timer);
  }, [draft, revision, saveState, selected, selectedId]);

  const updateDraft = (value: string) => {
    draftRef.current = value;
    setDraft(value);
    if (saveState === 'failed') setSaveState('unsaved');
  };

  const rememberEditorSelection = () => {
    const textarea = markdownEditorRef.current;
    if (!textarea) return editorSelectionRef.current;
    editorSelectionRef.current = {
      start: textarea.selectionStart,
      end: textarea.selectionEnd,
    };
    return editorSelectionRef.current;
  };

  const restoreEditorResult = (result: MarkdownEditResult) => {
    updateDraft(result.value);
    editorSelectionRef.current = {
      start: result.selectionStart,
      end: result.selectionEnd,
    };
    window.requestAnimationFrame(() => {
      const textarea = markdownEditorRef.current;
      if (!textarea) return;
      textarea.focus();
      textarea.setSelectionRange(result.selectionStart, result.selectionEnd);
    });
  };

  const applyEditorCommand = (command: MarkdownCommand) => {
    const selection = rememberEditorSelection();
    restoreEditorResult(applyMarkdownCommand(draftRef.current, selection, command));
  };

  const handleEditorKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing) return;
    const shortcut = commandForMarkdownShortcut(event);
    if (shortcut) {
      event.preventDefault();
      applyEditorCommand(shortcut);
      return;
    }
    const textarea = event.currentTarget;
    if (event.key === 'Tab') {
      event.preventDefault();
      const selection = {
        start: textarea.selectionStart,
        end: textarea.selectionEnd,
      };
      restoreEditorResult(indentMarkdown(draftRef.current, selection, event.shiftKey ? 'outdent' : 'indent'));
      return;
    }
    if (event.key === 'Enter' && !event.shiftKey && textarea.selectionStart === textarea.selectionEnd) {
      const continuation = continueMarkdownList(draftRef.current, textarea.selectionStart);
      if (continuation) {
        event.preventDefault();
        restoreEditorResult(continuation);
      }
    }
  };

  const insertArtifactMarkdown = (
    artifact: ReproductionArtifact,
    selectionOverride?: MarkdownSelection,
    draftOverride?: string,
  ) => {
    const source = draftOverride ?? draftRef.current;
    const selection = selectionOverride ?? editorSelectionRef.current;
    const url = reproductionApi.artifactUrl(artifact.projectId, artifact.id);
    const label = artifact.filename.replace(/[\[\]]/g, '\\$&');
    const markdown = artifact.mimeType.startsWith('image/') || artifact.kind === 'image'
      ? `![${label}](${url})`
      : `[${label}](${url})`;
    const start = Math.max(0, Math.min(source.length, selection.start));
    const end = Math.max(start, Math.min(source.length, selection.end));
    const before = source.slice(0, start);
    const after = source.slice(end);
    const beforeSeparator = before && !before.endsWith('\n') ? '\n\n' : '';
    const afterSeparator = after && !after.startsWith('\n') ? '\n\n' : after ? '\n' : '';
    const nextDraft = `${before}${beforeSeparator}${markdown}${afterSeparator}${after}`;
    const nextCursor = before.length + beforeSeparator.length + markdown.length;
    restoreEditorResult({ value: nextDraft, selectionStart: nextCursor, selectionEnd: nextCursor });
  };

  const uploadImageFile = async (file: File) => {
    if (!selected || !canEdit || detailLoading) return;
    if (editorUploadBusyRef.current) return;
    const mimeType = supportedImageMimeType(file);
    if (!mimeType) {
      setEditorUploadMessage('仅支持 PNG、JPEG 或 WebP 图片');
      setError('图片插入失败：仅支持 PNG、JPEG 或 WebP 图片。');
      return;
    }
    if (file.size > MAX_EDITOR_IMAGE_BYTES) {
      setEditorUploadMessage('图片过大 · 最大 25 MB');
      setError('图片插入失败：单个图片不能超过 25 MB。');
      return;
    }
    const source = draftRef.current;
    const selection = { ...rememberEditorSelection() };
    const projectId = selected.id;
    const filename = normalizedImageFilename(file, mimeType);
    const normalizedFile = file.type === mimeType && file.name === filename
      ? file
      : new File([file], filename, { type: mimeType, lastModified: file.lastModified });
    editorUploadBusyRef.current = true;
    setEditorUploadBusy(true);
    setEditorUploadMessage(`正在上传图片 · ${filename}`);
    try {
      const body = new FormData();
      body.append('file', normalizedFile, filename);
      body.append('kind', 'image');
      const artifact = await reproductionApi.uploadArtifact(projectId, body);
      if (selectedIdRef.current !== projectId) {
        setEditorUploadMessage(`图片已上传到项目 ${projectId.slice(-6)}`);
        notify('图片已上传，但原项目已切换；请回到原项目插入图片。');
        window.setTimeout(() => setEditorUploadMessage(''), 3600);
        return;
      }
      setArtifacts((current) => [artifact, ...current]);
      /* 上传期间若用户继续编辑，则把图片插入到最新光标位置，避免覆盖新输入。 */
      insertArtifactMarkdown(
        artifact,
        draftRef.current === source ? selection : rememberEditorSelection(),
        draftRef.current === source ? source : draftRef.current,
      );
      setEditorUploadMessage(`图片已插入 · ${filename}`);
      notify('图片已上传并插入文档');
      window.setTimeout(() => setEditorUploadMessage(''), 2600);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setEditorUploadMessage(`图片上传失败 · ${message}`);
      setError(`图片上传失败：${message}`);
    } finally {
      editorUploadBusyRef.current = false;
      setEditorUploadBusy(false);
    }
  };

  const handleEditorPaste = (event: ReactClipboardEvent<HTMLTextAreaElement>) => {
    const imageItem = [...event.clipboardData.items].find((item) => item.kind === 'file' && item.type.startsWith('image/'));
    const imageFile = imageItem?.getAsFile() ?? [...event.clipboardData.files].find((file) => file.type.startsWith('image/'));
    if (!imageFile) return;
    event.preventDefault();
    void uploadImageFile(imageFile);
  };

  const handleEditorDragEnter = (event: ReactDragEvent<HTMLTextAreaElement>) => {
    if (event.dataTransfer.types.includes('Files')) {
      event.preventDefault();
      editorDragDepthRef.current += 1;
      setEditorDragActive(true);
    }
  };

  const handleEditorDragOver = (event: ReactDragEvent<HTMLTextAreaElement>) => {
    if (!event.dataTransfer.types.includes('Files')) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  };

  const handleEditorDragLeave = (event: ReactDragEvent<HTMLTextAreaElement>) => {
    if (!event.dataTransfer.types.includes('Files')) return;
    event.preventDefault();
    editorDragDepthRef.current = Math.max(0, editorDragDepthRef.current - 1);
    if (editorDragDepthRef.current === 0) setEditorDragActive(false);
  };

  const handleEditorDrop = (event: ReactDragEvent<HTMLTextAreaElement>) => {
    if (!event.dataTransfer.files.length) return;
    event.preventDefault();
    editorDragDepthRef.current = 0;
    setEditorDragActive(false);
    const files = [...event.dataTransfer.files];
    const file = files.find((candidate) => Boolean(supportedImageMimeType(candidate))) ?? files[0];
    if (file) void uploadImageFile(file);
  };

  const retrySave = () => {
    if (!selectedId || draft === loadedContent.current) return;
    setError('');
    setSaveState('unsaved');
  };

  const hasPendingDocumentChanges = Boolean(
    selectedId && selected && (draft !== loadedContent.current || saveState !== 'saved'),
  );

  useEffect(() => {
    dirtyDocumentRef.current = hasPendingDocumentChanges;
  }, [hasPendingDocumentChanges]);

  useEffect(() => {
    if (!hasPendingDocumentChanges) return;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', handleBeforeUnload);
    return () => window.removeEventListener('beforeunload', handleBeforeUnload);
  }, [hasPendingDocumentChanges]);

  const selectProject = (id: string) => {
    if (listOnly) {
      onOpenProject?.(id);
      return;
    }
    if (id === selectedId) return;
    if (hasPendingDocumentChanges && !window.confirm('当前复现文档尚未保存，确定要切换项目吗？')) return;
    setSelectedId(id);
  };

  const outline = useMemo(() => headings(draft), [draft]);
  const availableTags = useMemo(
    () => [...new Set(projects.flatMap((project) => project.tags))].sort((a, b) => a.localeCompare(b, 'zh-CN')),
    [projects],
  );

  const jumpToHeading = (label: string, id: string) => {
    setActiveHeadingId(id);
    const root = editorRef.current;
    if (!root) return;
    const target = [...root.querySelectorAll<HTMLElement>('h1, h2, h3')].find((element) => element.textContent?.trim() === label);
    const reducedMotion = typeof window !== 'undefined'
      && typeof window.matchMedia === 'function'
      && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    target?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
  };

  const createProject = async () => {
    const name = createName.trim();
    if (!name) return;
    setCreateBusy(true);
    try {
      if (!createPaperId) {
        setError('请选择要关联的论文');
        return;
      }
      const project = await reproductionApi.create({ name, paperId: createPaperId, tags: parseTags(createTags) });
      setCreateOpen(false);
      setCreateName('');
      setCreatePaperId('');
      setCreateTags('');
      setProjects((current) => [project, ...current]);
      setSelectedId(project.id);
      notify('复现项目已创建');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setCreateBusy(false);
    }
  };

  const updateStatus = async (status: ReproductionStatus) => {
    if (!selected) return;
    try {
      const project = await reproductionApi.update(selected.id, { status, expectedRevision: selected.revision });
      setSelected(project);
      setProjects((current) => current.map((item) => item.id === project.id ? project : item));
      notify(`项目状态已更新为“${STATUS_LABELS[status]}”`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const openProjectEditor = () => {
    if (!selected || selected.status === 'archived') return;
    setEditName(selected.name);
    setEditTags(selected.tags.join(', '));
    setEditOpen(true);
  };

  const updateProjectDetails = async () => {
    if (!selected) return;
    const name = editName.trim();
    if (!name) return;
    setEditBusy(true);
    try {
      const project = await reproductionApi.update(selected.id, {
        name,
        tags: parseTags(editTags),
        expectedRevision: selected.revision,
      });
      setSelected(project);
      setProjects((current) => current.map((item) => item.id === project.id ? project : item));
      setEditOpen(false);
      notify('复现项目信息已更新');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setEditBusy(false);
    }
  };

  const archiveProject = async () => {
    if (!selected || !window.confirm(`归档“${selected.name}”？归档后仍可查看，但不会再编辑。`)) return;
    try {
      const project = await reproductionApi.archive(selected.id, selected.revision);
      setSelected(project);
      setProjects((current) => current.map((item) => item.id === project.id ? project : item));
      notify('项目已归档');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const deleteProject = async () => {
    if (selected?.status !== 'archived') return;
    if (!window.confirm(`永久删除“${selected.name}”？项目文档、运行记录和附件都会被删除，此操作不可撤销。`)) return;
    try {
      await reproductionApi.remove(selected.id, selected.revision);
      setSelectedId(null);
      await loadProjects();
      notify('复现项目已删除');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const recordRun = async () => {
    if (!selected) return;
    setRunBusy(true);
    try {
      let parameters: Record<string, unknown> = {};
      let metrics: Record<string, unknown> = {};
      try {
        parameters = runForm.parameters.trim() ? JSON.parse(runForm.parameters) as Record<string, unknown> : {};
        metrics = runForm.metrics.trim() ? JSON.parse(runForm.metrics) as Record<string, unknown> : {};
      } catch {
        setError('参数和指标必须是有效的 JSON 对象');
        setRunBusy(false);
        return;
      }
      const optionalText = (value: string) => value.trim() || undefined;
      const payload = {
        ...runForm,
        name: optionalText(runForm.name),
        environment: optionalText(runForm.environment),
        command: optionalText(runForm.command),
        dataVersion: optionalText(runForm.dataVersion),
        codeRevision: optionalText(runForm.codeRevision),
        startedAt: optionalText(runForm.startedAt),
        finishedAt: optionalText(runForm.finishedAt),
        runtimeVersions: optionalText(runForm.runtimeVersions),
        dataset: optionalText(runForm.dataset),
        preprocessing: optionalText(runForm.preprocessing),
        repositoryUrl: optionalText(runForm.repositoryUrl),
        config: optionalText(runForm.config),
        issues: optionalText(runForm.issues),
        parameters,
        metrics,
        seed: runForm.seed.trim() ? Number(runForm.seed) : null,
      };
      const run = runEditingId
        ? await reproductionApi.updateRun(selected.id, runEditingId, payload)
        : await reproductionApi.createRun(selected.id, payload);
      setRuns((current) => runEditingId ? current.map((item) => item.id === run.id ? run : item) : [run, ...current]);
      setRunOpen(false);
      setRunEditingId(null);
      setRunForm(EMPTY_RUN_FORM);
      notify(runEditingId ? '实验运行已更新' : '实验运行已记录');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRunBusy(false);
    }
  };

  const runFormFromRun = (run: ExperimentRun): RunFormState => ({
    name: run.name ?? '', environment: run.environment ?? '', command: run.command ?? '',
    parameters: JSON.stringify(run.parameters ?? {}, null, 2), dataVersion: run.dataVersion ?? '',
    codeRevision: run.codeRevision ?? '', seed: run.seed == null ? '' : String(run.seed), status: run.status,
    metrics: JSON.stringify(run.metrics ?? {}, null, 2), resultSummary: run.resultSummary ?? '',
    startedAt: run.startedAt ?? '', finishedAt: run.finishedAt ?? '', runtimeVersions: run.runtimeVersions ?? '',
    dataset: run.dataset ?? '', preprocessing: run.preprocessing ?? '', repositoryUrl: run.repositoryUrl ?? '',
    config: run.config ?? '', issues: run.issues ?? '',
  });

  const editRun = (run: ExperimentRun) => {
    setRunEditingId(run.id);
    setRunForm(runFormFromRun(run));
    setRunOpen(true);
  };

  const copyRun = (run: ExperimentRun) => {
    setRunEditingId(null);
    setRunForm({ ...runFormFromRun(run), name: `${run.name || run.resultSummary || '实验运行'} 副本`, status: 'planned', startedAt: '', finishedAt: '' });
    setRunOpen(true);
  };

  const deleteRun = async (run: ExperimentRun) => {
    if (!selected || !window.confirm(`删除实验运行“${run.name || run.resultSummary || '未命名运行'}”？关联附件不会被删除。`)) return;
    try {
      await reproductionApi.deleteRun(selected.id, run.id);
      setRuns((current) => current.filter((item) => item.id !== run.id));
      notify('实验运行已删除');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const recordResult = async () => {
    if (!selected || !resultForm.metricName.trim()) return;
    setResultBusy(true);
    try {
      const result = await reproductionApi.createResult(selected.id, {
        ...resultForm,
        metricName: resultForm.metricName.trim(),
      });
      setResults((current) => [result, ...current]);
      setResultForm({ metricName: '', paperValue: '', reproductionValue: '', difference: '', differencePercent: '', datasetSettings: '', source: '', status: 'not_reproduced', notes: '' });
      setResultOpen(false);
      notify('结果对照已保存');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setResultBusy(false);
    }
  };

  const addNote = async () => {
    if (!selected || !noteDraft.trim()) return;
    setNoteBusy(true);
    try {
      const note = await reproductionApi.addNote(selected.id, noteDraft.trim());
      setNotes((current) => [note, ...current]);
      setNoteDraft('');
      setNoteOpen(false);
      notify('复现笔记已添加');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setNoteBusy(false);
    }
  };

  const addArtifact = async () => {
    if (!selected || !artifactFile) return;
    setArtifactBusy(true);
    try {
      const body = new FormData();
      body.append('file', artifactFile);
      body.append('kind', artifactKind.trim() || 'attachment');
      const artifact = await reproductionApi.uploadArtifact(selected.id, body);
      setArtifacts((current) => [artifact, ...current]);
      insertArtifactMarkdown(artifact);
      setArtifactFile(null);
      setArtifactKind('attachment');
      setArtifactOpen(false);
      notify('附件信息已登记');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setArtifactBusy(false);
    }
  };

  const copyProject = async () => {
    if (!selected || !selected.paperId) {
      setError('该项目缺少关联论文，无法复制');
      return;
    }
    try {
      const copy = await reproductionApi.create({
        name: `${selected.name} 副本`,
        paperId: selected.paperId,
        tags: selected.tags,
      });
      if (draft && copy.document && draft !== copy.document.content) {
        const copiedDocument = await reproductionApi.saveDocument(copy.id, {
          content: draft,
          expectedRevision: copy.document.revision,
        });
        copy.document = copiedDocument;
      }
      setProjects((current) => [copy, ...current]);
      setSelectedId(copy.id);
      notify('复现项目副本已创建');
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  const canEdit = selected?.status !== 'archived';
  const paper = selected?.paperId ? papers.find((item) => item.id === selected.paperId) : undefined;
  const reloadSelected = async () => {
    if (!selectedId) return;
    await loadDetail(selectedId);
    setSaveConflict(false);
    setError('');
  };

  return (
    <div className={`page reproduction-page${listOnly ? ' reproduction-page--list' : ''}${detailOnly ? ' reproduction-page--detail' : ''}`}>
      {/* 列表态页头已移除：全局命令栏已承载页标题，「新建复现」并入下方工具栏 */}
      {!listOnly && <header className="reproduction__header">
        <div>
          {detailOnly && onBack && <button type="button" className="btn btn--ghost btn--sm reproduction__back" onClick={onBack}>← 返回论文复现列表</button>}
          <h1 className="display-title">{detailOnly && selected ? selected.name : '论文复现'}</h1>
          {detailOnly && selected && <p>{`关联论文：${selected.paperTitle || '未关联论文'} · 记录实验、结果与复现笔记`}</p>}
        </div>
        {!detailOnly && <button type="button" className="btn btn--primary" onClick={() => setCreateOpen(true)}><PlusIcon size={15} /> 新建复现</button>}
      </header>}

      {error && <div className="reproduction__error" role="alert"><span>{error}</span><div className="reproduction__error-actions">{saveState === 'failed' && !saveConflict && <button type="button" className="btn btn--ghost btn--sm" onClick={retrySave}>重试保存</button>}{saveConflict && <button type="button" className="btn btn--ghost btn--sm" onClick={() => void reloadSelected()}>重新加载最新版本</button>}<button type="button" className="btn btn--ghost btn--sm" onClick={() => setError('')}>关闭</button></div></div>}

      {listOnly && <div className="reproduction__toolbar">
        <label className="reproduction__search"><SearchIcon size={15} /><span className="sr-only">搜索复现项目</span><input className="input" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索项目或论文…" /></label>
        <label className="reproduction__filter"><span className="sr-only">项目状态</span><select className="input" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}><option value="">全部状态</option>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="reproduction__filter"><span className="sr-only">项目标签</span><select className="input" value={tagFilter} onChange={(event) => setTagFilter(event.target.value)}><option value="">全部标签</option>{availableTags.map((tag) => <option key={tag} value={tag}>{tag}</option>)}</select></label>
        <label className="reproduction__filter"><span className="sr-only">项目排序</span><select className="input" value={sort} onChange={(event) => setSort(event.target.value as 'updated' | 'created' | 'name')}><option value="updated">最近更新</option><option value="created">创建时间</option><option value="name">项目名称</option></select></label>
        <span className="reproduction__count">{loading ? '正在加载…' : `${projects.length} 个项目`}</span>
        <button type="button" className="btn btn--primary reproduction__toolbar-create" onClick={() => setCreateOpen(true)}><PlusIcon size={15} /> 新建复现</button>
      </div>}

      <div className="reproduction__workspace" ref={listScrollRef}>
        <aside className="reproduction__projects" aria-label="复现项目列表">
          <div className="reproduction__projects-heading"><span className="eyebrow">PROJECTS</span><span>{projects.length}</span></div>
          {loading ? <div className="reproduction__empty">正在加载复现项目…</div> : projects.length === 0 ? <div className="reproduction__empty"><DocumentIcon size={22} /><strong>{error ? '无法连接复现服务' : '还没有复现项目'}</strong><span>{error ? '请确认后端已启动后重试。' : '从一篇论文开始，记录你的复现过程。'}</span><button type="button" className="btn btn--primary btn--sm" onClick={() => setCreateOpen(true)}>新建项目</button></div> : <ul className="reproduction__project-list">{projects.map((project) => <ReproductionProjectCard key={project.id} project={project} active={project.id === selectedId} onSelect={selectProject} />)}</ul>}
        </aside>

        {!listOnly && !selected && !detailLoading && projects.length > 0 && <div className="reproduction__selection-empty"><DocumentIcon size={28} /><h2>选择一个复现项目</h2><p>从左侧选择项目，开始编辑复现文档或记录实验运行。</p></div>}

        {!listOnly && selected && <>
          <section className="reproduction__editor" aria-label="复现文档编辑器" aria-busy={detailLoading}>
            <div className="reproduction__editor-head"><div className="reproduction__editor-title"><h2>{selected.name}</h2><p>{paper ? <button type="button" className="reproduction__paper-link" onClick={() => openPaper(paper.id)}>↳ {paper.title_zh || paper.title}</button> : selected.paperTitle ? `↳ ${selected.paperTitle}` : '未关联论文'}</p></div><div className="reproduction__editor-actions"><span className={`reproduction__save reproduction__save--${saveState}`} role="status"><span aria-hidden="true" />{saveState === 'unsaved' ? '未保存' : saveState === 'saving' ? '保存中' : saveState === 'failed' ? '保存失败' : '已保存'}</span><div className="reproduction__segments" role="group" aria-label="文档显示模式">{(['edit', 'preview', 'split'] as const).map((value) => <button key={value} type="button" className={mode === value ? 'is-active' : ''} aria-pressed={mode === value} onClick={() => setMode(value)}>{value === 'edit' ? '编辑' : value === 'preview' ? '预览' : '分屏'}</button>)}</div></div></div>
            {(mode === 'edit' || mode === 'split') && <MarkdownToolbar disabled={!canEdit || detailLoading} uploading={editorUploadBusy} uploadMessage={editorUploadMessage} onCommand={applyEditorCommand} onImageFile={uploadImageFile} />}
            <div ref={editorRef} className={`reproduction__editor-body reproduction__editor-body--${mode}${editorDragActive ? ' is-drag-active' : ''}`}>
              {(mode === 'edit' || mode === 'split') && <textarea
                ref={markdownEditorRef}
                aria-label="复现 Markdown 正文"
                value={draft}
                disabled={!canEdit || detailLoading}
                placeholder="在此处输入。使用工具栏或 Markdown 进行格式化。拖放或粘贴图片。"
                spellCheck={false}
                onChange={(event) => updateDraft(event.target.value)}
                onSelect={rememberEditorSelection}
                onFocus={rememberEditorSelection}
                onClick={rememberEditorSelection}
                onBlur={rememberEditorSelection}
                onKeyUp={rememberEditorSelection}
                onKeyDown={handleEditorKeyDown}
                onPaste={handleEditorPaste}
                onDragEnter={handleEditorDragEnter}
                onDragOver={handleEditorDragOver}
                onDragLeave={handleEditorDragLeave}
                onDrop={handleEditorDrop}
              />}
              {(mode === 'preview' || mode === 'split') && <div className="reproduction__preview"><MarkdownView source={draft} /></div>}
            </div>
            <footer className="reproduction__editor-foot"><span>Markdown · KaTeX · 自动保存 · 修订 {revision}</span><span>{draft.length.toLocaleString()} 字</span></footer>
          </section>

          <aside className="reproduction__inspector" aria-label="复现项目大纲与摘要" aria-busy={detailLoading}>
            <section className="reproduction__inspector-section"><div className="reproduction__section-head"><div><h2>文档大纲</h2></div><span>{outline.length} 节</span></div>{outline.length === 0 ? <p className="reproduction__muted">在文档中添加 Markdown 标题后会显示章节导航。</p> : <nav aria-label="复现文档大纲"><ol className="reproduction__outline">{outline.map((item) => <li key={item.id} className={`reproduction__outline-level-${item.level}${activeHeadingId === item.id ? ' is-active' : ''}`}><button type="button" aria-current={activeHeadingId === item.id ? 'location' : undefined} onClick={() => jumpToHeading(item.label, item.id)}>{item.label}<ArrowRightIcon size={12} /></button></li>)}</ol></nav>}</section>
            <section className="reproduction__inspector-section reproduction__summary"><div className="reproduction__section-head"><div><h2>项目状态</h2></div></div><div className="reproduction__summary-status"><StatusBadge status={selected.status} /><select className="input input--sm" aria-label="项目状态" value={selected.status} disabled={selected.status === 'archived'} onChange={(event) => void updateStatus(event.target.value as ReproductionStatus)}>{Object.entries(STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></div><p>最后更新 {formatDate(selected.updatedAt)} · 修订 {selected.revision}</p></section>
            <section className="reproduction__inspector-section"><div className="reproduction__section-head"><div><h2>实验运行</h2></div><button type="button" className="btn btn--primary btn--sm" disabled={!canEdit} onClick={() => { setRunEditingId(null); setRunForm(EMPTY_RUN_FORM); setRunOpen(true); }}><SparkIcon size={13} /> 记录</button></div>{runs.length === 0 ? <p className="reproduction__muted">还没有实验运行记录。</p> : <ol className="reproduction__run-list">{runs.slice(0, runsExpanded ? runs.length : 4).map((run) => <li key={run.id}><span className={`reproduction__run-dot reproduction__run-dot--${run.status}`} aria-hidden="true" /><span className="reproduction__run-copy"><strong>{run.name || run.resultSummary || '实验运行'}</strong><small>{run.resultSummary && run.name ? `${run.resultSummary} · ` : ''}{run.environment || '未记录环境'} · {formatDate(run.createdAt)}</small></span><b>{RUN_STATUS_LABELS[run.status]}</b><span className="reproduction__run-actions"><button type="button" className="btn btn--ghost btn--xs" disabled={!canEdit} onClick={() => editRun(run)}>编辑</button><button type="button" className="btn btn--ghost btn--xs" disabled={!canEdit} onClick={() => copyRun(run)}>复制</button><button type="button" className="btn btn--ghost btn--xs reproduction__danger-action" disabled={!canEdit} onClick={() => void deleteRun(run)}>删除</button></span></li>)}</ol>}{runs.length > 4 && <button type="button" className="btn btn--ghost btn--sm reproduction__full-link" aria-expanded={runsExpanded} onClick={() => setRunsExpanded((expanded) => !expanded)}>{runsExpanded ? '收起运行记录' : `查看全部 ${runs.length} 次运行`} <ArrowRightIcon size={12} /></button>}</section>
            <section className="reproduction__inspector-section"><div className="reproduction__section-head"><div><h2>附件</h2></div><button type="button" className="btn btn--ghost btn--sm" disabled={!canEdit} onClick={() => setArtifactOpen(true)}><PlusIcon size={13} /> 添加</button></div>{artifacts.length === 0 ? <p className="reproduction__muted">尚未添加附件。</p> : <ul className="reproduction__artifact-list">{artifacts.slice(0, artifactsExpanded ? artifacts.length : 4).map((artifact) => <li key={artifact.id}><span><a href={reproductionApi.artifactUrl(artifact.projectId, artifact.id)} download={artifact.filename} aria-label={`下载附件 ${artifact.filename}`}><DownloadIcon size={12} /> {artifact.filename}</a><small>{Math.ceil(artifact.sizeBytes / 1024)} KB · {artifact.mimeType}</small></span><span className="reproduction__artifact-actions"><code>{artifact.kind}</code><button type="button" className="btn btn--ghost btn--xs" aria-label={`插入附件 ${artifact.filename}`} disabled={!canEdit} onClick={() => insertArtifactMarkdown(artifact)}>插入</button></span></li>)}</ul>}{artifacts.length > 4 && <button type="button" className="btn btn--ghost btn--sm reproduction__full-link" aria-expanded={artifactsExpanded} onClick={() => setArtifactsExpanded((expanded) => !expanded)}>{artifactsExpanded ? '收起附件' : `查看全部 ${artifacts.length} 个附件`} <ArrowRightIcon size={12} /></button>}</section>
            <section className="reproduction__inspector-section"><div className="reproduction__section-head"><div><h2>复现笔记</h2></div><button type="button" className="btn btn--ghost btn--sm" disabled={!canEdit} onClick={() => setNoteOpen(true)}>新增</button></div>{notes.length === 0 ? <p className="reproduction__muted">暂无独立笔记。</p> : <ul className="reproduction__note-list">{notes.slice(0, notesExpanded ? notes.length : 3).map((note) => <li key={note.id}><p>{note.content}</p><small>{formatDate(note.createdAt)}</small></li>)}</ul>}{notes.length > 3 && <button type="button" className="btn btn--ghost btn--sm reproduction__full-link" aria-expanded={notesExpanded} onClick={() => setNotesExpanded((expanded) => !expanded)}>{notesExpanded ? '收起笔记' : `查看全部 ${notes.length} 条笔记`} <ArrowRightIcon size={12} /></button>}</section>
            <section className="reproduction__inspector-actions"><button type="button" className="btn btn--ghost btn--sm" disabled={selected.status === 'archived'} onClick={openProjectEditor}><EditIcon size={14} /> 编辑项目</button><button type="button" className="btn btn--ghost btn--sm" onClick={() => void copyProject()}><DocumentIcon size={14} /> 复制项目</button><button type="button" className="btn btn--ghost btn--sm" disabled={selected.status === 'archived'} onClick={() => void archiveProject()}><ArchiveIcon size={14} /> 归档项目</button><button type="button" className="btn btn--ghost btn--sm reproduction__danger-action" disabled={selected.status !== 'archived'} onClick={() => void deleteProject()}><TrashIcon size={14} /> 删除项目</button></section>
          <section className="reproduction__inspector-section reproduction__results-section"><div className="reproduction__section-head"><div><h2>结果对照</h2></div><button type="button" className="btn btn--ghost btn--sm" disabled={!canEdit} onClick={() => setResultOpen(true)}><PlusIcon size={13} /> 记录指标</button></div>{results.length === 0 ? <p className="reproduction__muted">还没有原论文与复现结果的对照记录。</p> : <ul className="reproduction__result-list">{results.slice(0, 5).map((result) => <li key={result.id}><div><strong>{result.metricName}</strong><small>{result.paperValue ?? '—'} → {result.reproductionValue ?? '—'}{result.difference ? ` · ${result.difference}` : ''}</small></div><span className={`reproduction__result-status reproduction__result-status--${result.status}`}>{RESULT_STATUS_LABELS[result.status]}</span></li>)}</ul>}</section>
          </aside>
        </>}
      </div>

      {createOpen && <div className="reproduction__dialog-backdrop" role="presentation" onClick={(event) => { if (!createBusy && event.target === event.currentTarget) setCreateOpen(false); }}><form ref={createDialogRef} className="reproduction__dialog" role="dialog" aria-modal="true" aria-labelledby="create-reproduction-title" onSubmit={(event) => { event.preventDefault(); void createProject(); }}><div className="reproduction__dialog-head"><div><span className="eyebrow">NEW PROJECT</span><h2 id="create-reproduction-title">新建论文复现</h2></div><button type="button" className="btn btn--ghost btn--sm" aria-label="关闭新建复现对话框" disabled={createBusy} onClick={() => setCreateOpen(false)}><CloseIcon size={15} /></button></div><label>项目名称<input className="input" autoFocus required value={createName} onChange={(event) => setCreateName(event.target.value)} placeholder="例如：ViT 分阶段训练复现" /></label><label>关联论文（必选）<select className="input" required value={createPaperId} onChange={(event) => setCreatePaperId(event.target.value)}><option value="">请选择论文</option>{papers.map((item) => <option key={item.id} value={item.id}>{item.title_zh || item.title}</option>)}</select></label><label>标签（可选）<input className="input" value={createTags} onChange={(event) => setCreateTags(event.target.value)} placeholder="用逗号分隔，例如：视觉, 基线" /></label><p className="reproduction__dialog-hint">每个复现项目必须关联库内论文；创建后只生成空白模板，不会伪造实验内容。</p><div className="reproduction__dialog-actions"><button type="button" className="btn btn--ghost" disabled={createBusy} onClick={() => setCreateOpen(false)}>取消</button><button type="submit" className="btn btn--primary" disabled={createBusy || !createName.trim() || !createPaperId}>{createBusy ? '创建中…' : '创建项目'}</button></div></form></div>}

      {editOpen && <div className="reproduction__dialog-backdrop" role="presentation" onClick={(event) => { if (!editBusy && event.target === event.currentTarget) setEditOpen(false); }}><form ref={editDialogRef} className="reproduction__dialog" role="dialog" aria-modal="true" aria-labelledby="edit-reproduction-title" onSubmit={(event) => { event.preventDefault(); void updateProjectDetails(); }}><div className="reproduction__dialog-head"><div><span className="eyebrow">EDIT PROJECT</span><h2 id="edit-reproduction-title">编辑复现项目</h2></div><button type="button" className="btn btn--ghost btn--sm" aria-label="关闭编辑复现对话框" disabled={editBusy} onClick={() => setEditOpen(false)}><CloseIcon size={15} /></button></div><label>项目名称<input className="input" autoFocus required value={editName} onChange={(event) => setEditName(event.target.value)} /></label><label>标签（可选）<input className="input" value={editTags} onChange={(event) => setEditTags(event.target.value)} placeholder="用逗号分隔" /></label><p className="reproduction__dialog-hint">状态和正文在工作区中单独更新，归档项目不可编辑。</p><div className="reproduction__dialog-actions"><button type="button" className="btn btn--ghost" disabled={editBusy} onClick={() => setEditOpen(false)}>取消</button><button type="submit" className="btn btn--primary" disabled={editBusy || !editName.trim()}>{editBusy ? '保存中…' : '保存修改'}</button></div></form></div>}

      {runOpen && <div className="reproduction__dialog-backdrop" role="presentation" onClick={(event) => { if (!runBusy && event.target === event.currentTarget) setRunOpen(false); }}><form ref={runDialogRef} className="reproduction__dialog reproduction__dialog--wide" role="dialog" aria-modal="true" aria-labelledby="record-run-title" onSubmit={(event) => { event.preventDefault(); void recordRun(); }}><div className="reproduction__dialog-head"><div><span className="eyebrow">EXPERIMENT RUN</span><h2 id="record-run-title">{runEditingId ? '编辑实验运行' : '记录实验运行'}</h2></div><button type="button" className="btn btn--ghost btn--sm" aria-label="关闭记录运行对话框" disabled={runBusy} onClick={() => setRunOpen(false)}><CloseIcon size={15} /></button></div><div className="reproduction__form-grid">{([['name', '实验名称'], ['environment', '运行环境'], ['command', '命令'], ['dataVersion', '数据集版本'], ['codeRevision', '代码仓库 / commit'], ['seed', '随机种子'], ['startedAt', '开始时间'], ['finishedAt', '结束时间'], ['runtimeVersions', 'Python / CUDA / 依赖'], ['dataset', '数据集'], ['preprocessing', '预处理方式'], ['repositoryUrl', '代码仓库 URL']] as const).map(([key, label]) => <label key={key}>{label}<input className="input" autoFocus={key === 'name'} value={runForm[key]} onChange={(event) => setRunForm((current) => ({ ...current, [key]: event.target.value }))} /></label>)}<label>状态<select className="input" value={runForm.status} onChange={(event) => setRunForm((current) => ({ ...current, status: event.target.value as ExperimentRun['status'] }))}>{Object.entries(RUN_STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>指标 JSON<textarea className="input" rows={3} value={runForm.metrics} onChange={(event) => setRunForm((current) => ({ ...current, metrics: event.target.value }))} placeholder='{"accuracy": 0.81}' /></label><label>参数 JSON<textarea className="input" rows={3} value={runForm.parameters} onChange={(event) => setRunForm((current) => ({ ...current, parameters: event.target.value }))} placeholder='{"epochs": 10}' /></label><label className="reproduction__form-wide">配置文件 / 超参数<textarea className="input" rows={3} value={runForm.config} onChange={(event) => setRunForm((current) => ({ ...current, config: event.target.value }))} /></label><label className="reproduction__form-wide">结果摘要<textarea className="input" rows={3} value={runForm.resultSummary} onChange={(event) => setRunForm((current) => ({ ...current, resultSummary: event.target.value }))} /></label><label className="reproduction__form-wide">异常、偏差和问题<textarea className="input" rows={3} value={runForm.issues} onChange={(event) => setRunForm((current) => ({ ...current, issues: event.target.value }))} /></label></div><div className="reproduction__dialog-actions"><button type="button" className="btn btn--ghost" disabled={runBusy} onClick={() => setRunOpen(false)}>取消</button><button type="submit" className="btn btn--primary" disabled={runBusy}>{runBusy ? '保存中…' : runEditingId ? '保存修改' : '保存运行'}</button></div></form></div>}

      {noteOpen && <div className="reproduction__dialog-backdrop" role="presentation" onClick={(event) => { if (!noteBusy && event.target === event.currentTarget) setNoteOpen(false); }}><form ref={noteDialogRef} className="reproduction__dialog" role="dialog" aria-modal="true" aria-labelledby="add-note-title" onSubmit={(event) => { event.preventDefault(); void addNote(); }}><div className="reproduction__dialog-head"><div><span className="eyebrow">REPRODUCTION NOTE</span><h2 id="add-note-title">新增复现笔记</h2></div><button type="button" className="btn btn--ghost btn--sm" aria-label="关闭新增笔记对话框" disabled={noteBusy} onClick={() => setNoteOpen(false)}><CloseIcon size={15} /></button></div><label>笔记内容<textarea className="input" autoFocus rows={6} value={noteDraft} onChange={(event) => setNoteDraft(event.target.value)} placeholder="记录一个观察、偏差或下一步问题" /></label><div className="reproduction__dialog-actions"><button type="button" className="btn btn--ghost" disabled={noteBusy} onClick={() => setNoteOpen(false)}>取消</button><button type="submit" className="btn btn--primary" disabled={noteBusy || !noteDraft.trim()}>{noteBusy ? '保存中…' : '保存笔记'}</button></div></form></div>}


      {artifactOpen && <div className="reproduction__dialog-backdrop" role="presentation" onClick={(event) => { if (!artifactBusy && event.target === event.currentTarget) setArtifactOpen(false); }}><form ref={artifactDialogRef} className="reproduction__dialog" role="dialog" aria-modal="true" aria-labelledby="add-artifact-title" onSubmit={(event) => { event.preventDefault(); void addArtifact(); }}><div className="reproduction__dialog-head"><div><span className="eyebrow">REPRODUCTION ARTIFACT</span><h2 id="add-artifact-title">添加附件</h2></div><button type="button" className="btn btn--ghost btn--sm" aria-label="关闭添加附件对话框" disabled={artifactBusy} onClick={() => setArtifactOpen(false)}><CloseIcon size={15} /></button></div><label>附件文件<input className="input" autoFocus type="file" accept=".txt,.log,.md,.markdown,.csv,.json,.pdf,.png,.jpg,.jpeg,.webp" onChange={(event) => setArtifactFile(event.target.files?.[0] ?? null)} /></label><label>附件类型<select className="input" value={artifactKind} onChange={(event) => setArtifactKind(event.target.value)}><option value="attachment">附件</option><option value="log">日志</option><option value="table">表格</option><option value="image">图片</option><option value="document">文档</option></select></label><p className="reproduction__dialog-hint">支持文本、Markdown、CSV、JSON、PDF、PNG、JPEG 和 WebP，单个文件不超过 25 MB。图片也可直接用工具栏上传，或拖放 / 粘贴到正文。</p><div className="reproduction__dialog-actions"><button type="button" className="btn btn--ghost" disabled={artifactBusy} onClick={() => setArtifactOpen(false)}>取消</button><button type="submit" className="btn btn--primary" disabled={artifactBusy || !artifactFile}>{artifactBusy ? '上传中…' : '上传附件'}</button></div></form></div>}
      {resultOpen && (
        <div className="reproduction__dialog-backdrop" role="presentation" onClick={(event) => { if (!resultBusy && event.target === event.currentTarget) setResultOpen(false); }}>
          <form ref={resultDialogRef} className="reproduction__dialog reproduction__dialog--wide" role="dialog" aria-modal="true" aria-labelledby="add-result-title" onSubmit={(event) => { event.preventDefault(); void recordResult(); }}>
            <div className="reproduction__dialog-head"><div><span className="eyebrow">RESULT COMPARISON</span><h2 id="add-result-title">记录结果对照</h2></div><button type="button" className="btn btn--ghost btn--sm" aria-label="关闭结果对照对话框" disabled={resultBusy} onClick={() => setResultOpen(false)}><CloseIcon size={15} /></button></div>
            <div className="reproduction__form-grid">
              <label>指标名称<input className="input" autoFocus required value={resultForm.metricName} onChange={(event) => setResultForm((current) => ({ ...current, metricName: event.target.value }))} /></label>
              <label>结果状态<select className="input" value={resultForm.status} onChange={(event) => setResultForm((current) => ({ ...current, status: event.target.value as ReproductionResultStatus }))}>{Object.entries(RESULT_STATUS_LABELS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
              {([['paperValue', '原论文结果'], ['reproductionValue', '复现结果'], ['difference', '差值'], ['differencePercent', '差异百分比'], ['datasetSettings', '数据集 / 设置'], ['source', '结果来源']] as const).map(([key, label]) => <label key={key}>{label}<input className="input" value={resultForm[key]} onChange={(event) => setResultForm((current) => ({ ...current, [key]: event.target.value }))} /></label>)}
              <label className="reproduction__form-wide">备注与差异原因<textarea className="input" rows={4} value={resultForm.notes} onChange={(event) => setResultForm((current) => ({ ...current, notes: event.target.value }))} placeholder="数据集、预处理、随机种子、环境或实现差异" /></label>
            </div>
            <div className="reproduction__dialog-actions"><button type="button" className="btn btn--ghost" disabled={resultBusy} onClick={() => setResultOpen(false)}>取消</button><button type="submit" className="btn btn--primary" disabled={resultBusy || !resultForm.metricName.trim()}>{resultBusy ? '保存中…' : '保存对照'}</button></div>
          </form>
        </div>
      )}
    </div>
  );
}
