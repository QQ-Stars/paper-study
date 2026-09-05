import { useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';

import {
  CodeIcon,
  HeadingIcon,
  HorizontalRuleIcon,
  LinkIcon,
  ListIcon,
  MoreIcon,
  OrderedListIcon,
  QuoteIcon,
  SparkIcon,
  SmileIcon,
  StrikethroughIcon,
  TableIcon,
  UploadIcon,
} from './Icons';
import type { MarkdownCommand } from './markdownEditor';

interface MarkdownToolbarProps {
  disabled?: boolean;
  uploading?: boolean;
  uploadMessage?: string;
  onCommand: (command: MarkdownCommand) => void;
  onImageFile: (file: File) => void;
}

type ToolbarButtonProps = {
  label: string;
  shortcut?: string;
  disabled: boolean;
  onClick: () => void;
  children: ReactNode;
};

function ariaShortcut(shortcut?: string): string | undefined {
  if (!shortcut) return undefined;
  return shortcut
    .replace('⌘/Ctrl ', 'Control+')
    .replace('⇧', 'Shift')
    .replace(/\s+/g, '+');
}

function ToolbarButton({ label, shortcut, disabled, onClick, children }: ToolbarButtonProps) {
  const title = shortcut ? `${label} · ${shortcut}` : label;
  return (
    <button
      type="button"
      className="reproduction__editor-tool"
      aria-label={label}
      title={title}
      aria-keyshortcuts={ariaShortcut(shortcut)}
      disabled={disabled}
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export function MarkdownToolbar({
  disabled = false,
  uploading = false,
  uploadMessage,
  onCommand,
  onImageFile,
}: MarkdownToolbarProps) {
  const [moreOpen, setMoreOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const imageInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!moreOpen) return;
    const closeOnOutside = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setMoreOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMoreOpen(false);
    };
    document.addEventListener('mousedown', closeOnOutside);
    document.addEventListener('keydown', closeOnEscape);
    return () => {
      document.removeEventListener('mousedown', closeOnOutside);
      document.removeEventListener('keydown', closeOnEscape);
    };
  }, [moreOpen]);

  const runCommand = (command: MarkdownCommand) => {
    onCommand(command);
    setMoreOpen(false);
  };

  return (
    <div ref={rootRef} className="reproduction__editor-toolbar" role="toolbar" aria-label="Markdown 编辑工具栏" aria-busy={uploading}>
      <div className="reproduction__editor-tool-group reproduction__editor-tool-group--primary">
        <span className="reproduction__editor-mode" aria-label="Markdown 编辑模式">
          <span className="reproduction__editor-mode-mark" aria-hidden="true">M</span>
          <span>Markdown</span>
        </span>
        <span className="reproduction__editor-tool-divider" aria-hidden="true" />
        <ToolbarButton label="粗体" shortcut="⌘/Ctrl B" disabled={disabled} onClick={() => runCommand('bold')}><strong>B</strong></ToolbarButton>
        <ToolbarButton label="斜体" shortcut="⌘/Ctrl I" disabled={disabled} onClick={() => runCommand('italic')}><em>I</em></ToolbarButton>
        <ToolbarButton label="二级标题" disabled={disabled} onClick={() => runCommand('heading')}><HeadingIcon size={16} /></ToolbarButton>
        <ToolbarButton label="插入链接" shortcut="⌘/Ctrl K" disabled={disabled} onClick={() => runCommand('link')}><LinkIcon size={16} /></ToolbarButton>
        <ToolbarButton label="引用" disabled={disabled} onClick={() => runCommand('quote')}><QuoteIcon size={16} /></ToolbarButton>
      </div>

      <div className="reproduction__editor-tool-group reproduction__editor-tool-group--secondary">
        <ToolbarButton label={uploading ? '图片上传中' : '上传图片'} disabled={disabled || uploading} onClick={() => imageInputRef.current?.click()}>
          {uploading ? <span className="reproduction__editor-tool-spinner" aria-hidden="true" /> : <UploadIcon size={16} />}
        </ToolbarButton>
        <ToolbarButton label="无序列表" shortcut="⌘/Ctrl Shift 8" disabled={disabled} onClick={() => runCommand('unordered-list')}><ListIcon size={16} /></ToolbarButton>
        <ToolbarButton label="有序列表" shortcut="⌘/Ctrl Shift 7" disabled={disabled} onClick={() => runCommand('ordered-list')}><OrderedListIcon size={16} /></ToolbarButton>
        <ToolbarButton label="插入表格" shortcut="⌘/Ctrl Alt T" disabled={disabled} onClick={() => runCommand('table')}><TableIcon size={16} /></ToolbarButton>
        <ToolbarButton label="插入分隔线" disabled={disabled} onClick={() => runCommand('divider')}><HorizontalRuleIcon size={16} /></ToolbarButton>
        <div className="reproduction__editor-more">
          <button
            type="button"
            className={`reproduction__editor-tool${moreOpen ? ' is-active' : ''}`}
            aria-label="更多 Markdown 工具"
            aria-expanded={moreOpen}
            aria-haspopup="menu"
            title="更多 Markdown 工具"
            disabled={disabled}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => setMoreOpen((open) => !open)}
          >
            <MoreIcon size={16} />
          </button>
          {moreOpen && <div className="reproduction__editor-more-menu" role="menu" aria-label="更多 Markdown 工具">
            <button type="button" role="menuitem" onMouseDown={(event) => event.preventDefault()} onClick={() => runCommand('code')}><CodeIcon size={15} /><span>代码块</span><kbd>⌘/Ctrl `</kbd></button>
            <button type="button" role="menuitem" onMouseDown={(event) => event.preventDefault()} onClick={() => runCommand('mermaid')}><SparkIcon size={15} /><span>Mermaid 图表</span></button>
            <button type="button" role="menuitem" onMouseDown={(event) => event.preventDefault()} onClick={() => runCommand('strike')}><StrikethroughIcon size={15} /><span>删除线</span><kbd>⌘/Ctrl ⇧ X</kbd></button>
            <button type="button" role="menuitem" onMouseDown={(event) => event.preventDefault()} onClick={() => runCommand('emoji')}><SmileIcon size={15} /><span>插入符号</span></button>
            <div className="reproduction__editor-shortcuts" role="note">
              <strong>快捷键</strong>
              <span>Ctrl/⌘ B 粗体 · I 斜体 · K 链接 · Alt+T 表格</span>
              <span>Tab / ⇧Tab 缩进 · Enter 延续列表</span>
            </div>
          </div>}
        </div>
      </div>

      <span className="reproduction__editor-toolbar-hint">拖放或粘贴图片 · 自动保存</span>
      {uploadMessage && <span className="reproduction__editor-upload-status" role="status" aria-live="polite">{uploadMessage}</span>}
      <input
        ref={imageInputRef}
        className="reproduction__editor-image-input"
        type="file"
        accept="image/png,image/jpeg,image/webp"
        tabIndex={-1}
        aria-hidden="true"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.currentTarget.value = '';
          if (file) onImageFile(file);
        }}
      />
    </div>
  );
}
