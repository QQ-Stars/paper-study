import {
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
} from 'react';
import { createPortal } from 'react-dom';

import { focusFirstWithin, focusSafely, trapTabKey } from '../../lib/accessibility/focus';
import type { PaperDraft } from '../../lib/api/paperApi';

export interface PaperEditorProps {
  readonly mode: 'create' | 'edit';
  readonly initialDraft?: PaperDraft;
  readonly pending?: boolean;
  readonly error?: string;
  readonly onCancel: () => void;
  readonly onSubmit: (draft: PaperDraft) => void;
}

interface EditorFields {
  title: string;
  titleZh: string;
  authors: string;
  venue: string;
  year: string;
  type: string;
  topic: string;
  url: string;
  pdfUrl: string;
  pdfPath: string;
  tldr: string;
  abstract: string;
  contribution: string;
}

function text(value: string | null | undefined): string {
  return value ?? '';
}

function createFields(initialDraft?: PaperDraft): EditorFields {
  return {
    title: text(initialDraft?.title),
    titleZh: text(initialDraft?.titleZh),
    authors: initialDraft?.authors?.join(', ') ?? '',
    venue: text(initialDraft?.venue),
    year: text(initialDraft?.year),
    type: text(initialDraft?.type),
    topic: text(initialDraft?.topic),
    url: text(initialDraft?.url),
    pdfUrl: text(initialDraft?.pdfUrl),
    pdfPath: text(initialDraft?.pdfPath),
    tldr: text(initialDraft?.tldr),
    abstract: text(initialDraft?.abstract),
    contribution: text(initialDraft?.contribution),
  };
}

function optional(value: string): string | null {
  const normalized = value.trim();
  return normalized || null;
}

export function PaperEditor({
  mode,
  initialDraft,
  pending = false,
  error = '',
  onCancel,
  onSubmit,
}: PaperEditorProps) {
  const [fields, setFields] = useState<EditorFields>(() => createFields(initialDraft));
  const [validationError, setValidationError] = useState('');
  const dialogRef = useRef<HTMLElement>(null);
  const returnFocusRef = useRef(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  );
  const titleId = useId();
  const descriptionId = useId();
  const dialogTitle = mode === 'create' ? '添加论文' : '编辑论文';

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const returnFocus = returnFocusRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    focusFirstWithin(dialog);

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !pending) {
        event.preventDefault();
        onCancel();
        return;
      }
      trapTabKey(dialog, event);
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
      focusSafely(returnFocus);
    };
  }, [onCancel, pending]);

  const update = (field: keyof EditorFields, value: string) => {
    setFields((current) => ({ ...current, [field]: value }));
    if (field === 'title') setValidationError('');
  };
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const title = fields.title.trim();
    if (!title) {
      setValidationError('英文题名不能为空');
      return;
    }
    onSubmit({
      title,
      titleZh: optional(fields.titleZh),
      authors: fields.authors
        .split(/[,，;\n]/)
        .map((author) => author.trim())
        .filter(Boolean),
      venue: optional(fields.venue),
      year: optional(fields.year),
      type: optional(fields.type),
      topic: optional(fields.topic),
      url: optional(fields.url),
      pdfUrl: optional(fields.pdfUrl),
      pdfPath: optional(fields.pdfPath),
      tldr: optional(fields.tldr),
      abstract: optional(fields.abstract),
      contribution: optional(fields.contribution),
    });
  };
  const visibleError = error || validationError;

  return createPortal(
    <div
      className="paper-editor-backdrop"
      onPointerDown={(event) => {
        if (event.currentTarget === event.target && !pending) onCancel();
      }}
    >
      <section
        ref={dialogRef}
        className="paper-editor floating-material"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={-1}
      >
        <header className="paper-editor__header">
          <div>
            <p>LIBRARY RECORD</p>
            <h2 id={titleId}>{dialogTitle}</h2>
            <span id={descriptionId}>维护论文的可核验元数据，不会自动改写阅读与复习状态。</span>
          </div>
          <button type="button" disabled={pending} onClick={onCancel} aria-label={`关闭${dialogTitle}`}>
            关闭
          </button>
        </header>

        <form className="paper-editor__form" onSubmit={submit}>
          <div className="paper-editor__fields">
            <label className="paper-editor__field paper-editor__field--wide">
              <span>英文题名</span>
              <input
                data-panel-autofocus="true"
                required
                value={fields.title}
                onChange={(event) => update('title', event.target.value)}
              />
            </label>
            <label className="paper-editor__field paper-editor__field--wide">
              <span>中文题名</span>
              <input value={fields.titleZh} onChange={(event) => update('titleZh', event.target.value)} />
            </label>
            <label className="paper-editor__field paper-editor__field--wide">
              <span>作者</span>
              <input value={fields.authors} onChange={(event) => update('authors', event.target.value)} />
            </label>
            <label className="paper-editor__field">
              <span>会议或来源</span>
              <input value={fields.venue} onChange={(event) => update('venue', event.target.value)} />
            </label>
            <label className="paper-editor__field">
              <span>年份</span>
              <input inputMode="numeric" value={fields.year} onChange={(event) => update('year', event.target.value)} />
            </label>
            <label className="paper-editor__field">
              <span>类型</span>
              <input value={fields.type} onChange={(event) => update('type', event.target.value)} />
            </label>
            <label className="paper-editor__field">
              <span>主题</span>
              <input value={fields.topic} onChange={(event) => update('topic', event.target.value)} />
            </label>
            <label className="paper-editor__field paper-editor__field--wide">
              <span>论文链接</span>
              <input type="url" value={fields.url} onChange={(event) => update('url', event.target.value)} />
            </label>
            <label className="paper-editor__field paper-editor__field--wide">
              <span>PDF 链接</span>
              <input type="url" value={fields.pdfUrl} onChange={(event) => update('pdfUrl', event.target.value)} />
            </label>
            <label className="paper-editor__field paper-editor__field--wide">
              <span>本地 PDF 路径</span>
              <input value={fields.pdfPath} onChange={(event) => update('pdfPath', event.target.value)} />
            </label>
            <label className="paper-editor__field paper-editor__field--wide">
              <span>一句话总结</span>
              <input value={fields.tldr} onChange={(event) => update('tldr', event.target.value)} />
            </label>
            <label className="paper-editor__field paper-editor__field--wide">
              <span>摘要</span>
              <textarea rows={4} value={fields.abstract} onChange={(event) => update('abstract', event.target.value)} />
            </label>
            <label className="paper-editor__field paper-editor__field--wide">
              <span>核心贡献</span>
              <textarea rows={3} value={fields.contribution} onChange={(event) => update('contribution', event.target.value)} />
            </label>
          </div>

          <footer className="paper-editor__footer">
            <div className="paper-editor__feedback" aria-live="polite">
              {visibleError ? <p role="alert">保存失败：{visibleError}</p> : pending ? <p>正在保存论文记录…</p> : null}
            </div>
            <div>
              <button type="button" disabled={pending} onClick={onCancel}>取消</button>
              <button type="submit" className="paper-editor__save" disabled={pending}>
                {pending ? '保存中…' : '保存论文'}
              </button>
            </div>
          </footer>
        </form>
      </section>
    </div>,
    document.body,
  );
}

export interface PaperDeleteConfirmationProps {
  readonly paperTitle: string;
  readonly pending?: boolean;
  readonly error?: string;
  readonly onCancel: () => void;
  readonly onConfirm: () => void;
}

export function PaperDeleteConfirmation({
  paperTitle,
  pending = false,
  error = '',
  onCancel,
  onConfirm,
}: PaperDeleteConfirmationProps) {
  const dialogRef = useRef<HTMLElement>(null);
  const returnFocusRef = useRef(
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  );
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    const returnFocus = returnFocusRef.current;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    focusFirstWithin(dialog);

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && !pending) {
        event.preventDefault();
        onCancel();
        return;
      }
      trapTabKey(dialog, event);
    };
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
      focusSafely(returnFocus);
    };
  }, [onCancel, pending]);

  return createPortal(
    <div
      className="paper-editor-backdrop"
      onPointerDown={(event) => {
        if (event.currentTarget === event.target && !pending) onCancel();
      }}
    >
      <section
        ref={dialogRef}
        className="paper-editor paper-delete-confirmation floating-material"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descriptionId}
        tabIndex={-1}
      >
        <header className="paper-editor__header">
          <div>
            <p>DESTRUCTIVE ACTION</p>
            <h2 id={titleId}>删除 {paperTitle}</h2>
            <span id={descriptionId}>提交前可以取消。服务端失败时会保留论文与全部本地上下文。</span>
          </div>
          <button type="button" disabled={pending} onClick={onCancel} aria-label="关闭删除确认">
            关闭
          </button>
        </header>

        <div className="paper-delete-confirmation__body">
          <strong>确认移除此论文记录？</strong>
          <p>成功后会同时移除实体缓存；后端也可能删除对应的托管 PDF 文件。</p>
        </div>

        <footer className="paper-editor__footer">
          <div className="paper-editor__feedback" aria-live="polite">
            {error ? <p role="alert">删除失败：{error}</p> : pending ? <p>正在等待服务端确认…</p> : null}
          </div>
          <div>
            <button type="button" disabled={pending} onClick={onCancel}>取消</button>
            <button
              type="button"
              className="paper-delete-confirmation__confirm"
              disabled={pending}
              onClick={onConfirm}
            >
              {pending ? '删除中…' : '确认删除'}
            </button>
          </div>
        </footer>
      </section>
    </div>,
    document.body,
  );
}
