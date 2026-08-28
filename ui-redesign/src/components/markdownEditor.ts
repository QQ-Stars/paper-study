export type MarkdownCommand =
  | 'bold'
  | 'italic'
  | 'strike'
  | 'heading'
  | 'link'
  | 'quote'
  | 'code'
  | 'unordered-list'
  | 'ordered-list'
  | 'table'
  | 'divider'
  | 'emoji';

export interface MarkdownEditResult {
  value: string;
  selectionStart: number;
  selectionEnd: number;
}

export interface MarkdownSelection {
  start: number;
  end: number;
}

export interface MarkdownShortcutEvent {
  key: string;
  code?: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
  isComposing?: boolean;
}

export interface ImageFileLike {
  name?: string | null;
  type?: string | null;
}

export const SUPPORTED_IMAGE_MIME_TYPES = [
  'image/png',
  'image/jpeg',
  'image/webp',
] as const;

export type SupportedImageMimeType = (typeof SUPPORTED_IMAGE_MIME_TYPES)[number];

const IMAGE_MIME_BY_EXTENSION: Record<string, SupportedImageMimeType> = {
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.webp': 'image/webp',
};

const IMAGE_EXTENSION_BY_MIME: Record<SupportedImageMimeType, string> = {
  'image/png': '.png',
  'image/jpeg': '.jpg',
  'image/webp': '.webp',
};

const SUPPORTED_IMAGE_MIME_SET = new Set<string>(SUPPORTED_IMAGE_MIME_TYPES);

function clampSelection(value: string, selection: MarkdownSelection): MarkdownSelection {
  const start = Math.max(0, Math.min(value.length, Number.isFinite(selection.start) ? selection.start : 0));
  const end = Math.max(start, Math.min(value.length, Number.isFinite(selection.end) ? selection.end : start));
  return { start, end };
}

function editResult(value: string, selectionStart: number, selectionEnd = selectionStart): MarkdownEditResult {
  return { value, selectionStart, selectionEnd };
}

function lineRange(value: string, selection: MarkdownSelection): { start: number; end: number } {
  const safe = clampSelection(value, selection);
  const start = value.lastIndexOf('\n', Math.max(0, safe.start - 1)) + 1;
  /* SelectionEnd at the beginning of a newline should not pull in the next line. */
  const endCursor = safe.end > 0 && value[safe.end - 1] === '\n' ? safe.end - 1 : safe.end;
  const endOfSelectionLine = value.indexOf('\n', endCursor);
  const end = endOfSelectionLine === -1 ? value.length : endOfSelectionLine;
  return { start, end };
}

function withBlockSpacing(value: string, selection: MarkdownSelection, block: string): MarkdownEditResult {
  const safe = clampSelection(value, selection);
  const before = value.slice(0, safe.start);
  const after = value.slice(safe.end);
  const beforeSeparator = before && !before.endsWith('\n\n') ? (before.endsWith('\n') ? '\n' : '\n\n') : '';
  const afterSeparator = after && !after.startsWith('\n\n') ? (after.startsWith('\n') ? '\n' : '\n\n') : '';
  const insertionStart = before.length + beforeSeparator.length;
  const next = `${before}${beforeSeparator}${block}${afterSeparator}${after}`;
  return editResult(next, insertionStart + block.length, insertionStart + block.length);
}

function wrapInline(
  value: string,
  selection: MarkdownSelection,
  marker: string,
  placeholder: string,
): MarkdownEditResult {
  const safe = clampSelection(value, selection);
  const selected = value.slice(safe.start, safe.end);
  if (!selected) {
    const insertion = `${marker}${placeholder}${marker}`;
    return editResult(
      `${value.slice(0, safe.start)}${insertion}${value.slice(safe.end)}`,
      safe.start + marker.length,
      safe.start + marker.length + placeholder.length,
    );
  }
  if (selected.startsWith(marker) && selected.endsWith(marker) && selected.length >= marker.length * 2) {
    const unwrapped = selected.slice(marker.length, -marker.length);
    return editResult(`${value.slice(0, safe.start)}${unwrapped}${value.slice(safe.end)}`, safe.start, safe.start + unwrapped.length);
  }
  const replacement = `${marker}${selected}${marker}`;
  const next = `${value.slice(0, safe.start)}${replacement}${value.slice(safe.end)}`;
  return editResult(next, safe.start + marker.length, safe.start + marker.length + selected.length);
}

function applyLinePrefix(
  value: string,
  selection: MarkdownSelection,
  prefix: string,
  matcher: RegExp,
  placeholder: string,
): MarkdownEditResult {
  const safe = clampSelection(value, selection);
  const range = lineRange(value, safe);
  const block = value.slice(range.start, range.end);
  const lines = block.split('\n');
  const hasContent = lines.some((line) => line.trim().length > 0);
  if (!hasContent && lines.length === 1) {
    const insertion = `${prefix}${placeholder}`;
    const next = `${value.slice(0, range.start)}${insertion}${value.slice(range.end)}`;
    return editResult(next, range.start + prefix.length, range.start + insertion.length);
  }
  const shouldRemove = lines.every((line) => line.trim() === '' || matcher.test(line));
  const transformed = lines.map((line) => {
    if (shouldRemove) return line.replace(matcher, '$1');
    return matcher.test(line) ? line : `${prefix}${line}`;
  });
  const replacement = transformed.join('\n');
  const next = `${value.slice(0, range.start)}${replacement}${value.slice(range.end)}`;
  /* 选择整个被格式化的行块，便于连续点击列表/引用按钮继续切换。 */
  return editResult(next, range.start, range.start + replacement.length);
}

function applyHeading(value: string, selection: MarkdownSelection, level = 2): MarkdownEditResult {
  const safe = clampSelection(value, selection);
  const range = lineRange(value, safe);
  const block = value.slice(range.start, range.end);
  const lines = block.split('\n');
  const heading = `${'#'.repeat(Math.max(1, Math.min(6, level)))} `;
  if (lines.length === 1 && !lines[0].trim()) {
    const insertion = `${heading}标题`;
    const next = `${value.slice(0, range.start)}${insertion}${value.slice(range.end)}`;
    return editResult(next, range.start + heading.length, range.start + insertion.length);
  }
  const transformed = lines.map((line) => {
    const existing = line.match(/^(\s*)(#{1,6})\s+/);
    if (existing && existing[2].length === heading.trim().length) return line.replace(/^(\s*)#{1,6}\s+/, '$1');
    return existing ? line.replace(/^(\s*)#{1,6}\s+/, `$1${heading}`) : `${heading}${line}`;
  });
  const replacement = transformed.join('\n');
  const next = `${value.slice(0, range.start)}${replacement}${value.slice(range.end)}`;
  return editResult(next, range.start, range.start + replacement.length);
}

function applyLink(value: string, selection: MarkdownSelection): MarkdownEditResult {
  const safe = clampSelection(value, selection);
  const selected = value.slice(safe.start, safe.end);
  if (selected) {
    const replacement = `[${selected}](https://)`;
    const next = `${value.slice(0, safe.start)}${replacement}${value.slice(safe.end)}`;
    const urlStart = safe.start + selected.length + 3;
    return editResult(next, urlStart, urlStart + 8);
  }
  const replacement = '[链接文字](https://)';
  const next = `${value.slice(0, safe.start)}${replacement}${value.slice(safe.end)}`;
  return editResult(next, safe.start + 1, safe.start + 5);
}

function applyCode(value: string, selection: MarkdownSelection): MarkdownEditResult {
  const safe = clampSelection(value, selection);
  const selected = value.slice(safe.start, safe.end);
  if (selected && selected.includes('\n')) {
    return withBlockSpacing(value, safe, `\`\`\`\n${selected}\n\`\`\``);
  }
  return wrapInline(value, safe, '`', '代码');
}

function applyTable(value: string, selection: MarkdownSelection): MarkdownEditResult {
  const table = '| 列 1 | 列 2 | 列 3 |\n| --- | --- | --- |\n| 内容 | 内容 | 内容 |';
  const result = withBlockSpacing(value, selection, table);
  const firstCell = table.indexOf('内容');
  return editResult(result.value, result.selectionStart - table.length + firstCell, result.selectionStart - table.length + firstCell + 2);
}

export function applyMarkdownCommand(
  value: string,
  selection: MarkdownSelection,
  command: MarkdownCommand,
): MarkdownEditResult {
  switch (command) {
    case 'bold':
      return wrapInline(value, selection, '**', '粗体文字');
    case 'italic':
      return wrapInline(value, selection, '*', '斜体文字');
    case 'strike':
      return wrapInline(value, selection, '~~', '删除线');
    case 'heading':
      return applyHeading(value, selection);
    case 'link':
      return applyLink(value, selection);
    case 'quote':
      return applyLinePrefix(value, selection, '> ', /^(\s*)>\s+/, '引用文字');
    case 'code':
      return applyCode(value, selection);
    case 'unordered-list':
      return applyLinePrefix(value, selection, '- ', /^(\s*)[-*+]\s+/, '列表项');
    case 'ordered-list':
      return applyLinePrefix(value, selection, '1. ', /^(\s*)\d+[.)]\s+/, '列表项');
    case 'table':
      return applyTable(value, selection);
    case 'divider':
      return withBlockSpacing(value, selection, '---');
    case 'emoji': {
      const safe = clampSelection(value, selection);
      const emoji = '✨';
      return editResult(`${value.slice(0, safe.start)}${emoji}${value.slice(safe.end)}`, safe.start + emoji.length);
    }
  }
}

export function indentMarkdown(
  value: string,
  selection: MarkdownSelection,
  direction: 'indent' | 'outdent',
): MarkdownEditResult {
  const safe = clampSelection(value, selection);
  const range = lineRange(value, safe);
  const block = value.slice(range.start, range.end);
  const lines = block.split('\n');
  const transformed = direction === 'indent'
    ? lines.map((line) => `  ${line}`)
    : lines.map((line) => line.replace(/^( {1,2}|\t)/, ''));
  const replacement = transformed.join('\n');
  const next = `${value.slice(0, range.start)}${replacement}${value.slice(range.end)}`;
  const startDelta = transformed[0].length - lines[0].length;
  const endDelta = replacement.length - block.length;
  const start = Math.max(range.start, safe.start + startDelta);
  const end = Math.max(start, safe.end + endDelta);
  return editResult(next, start, end);
}

export function continueMarkdownList(value: string, caret: number): MarkdownEditResult | null {
  const safe = clampSelection(value, { start: caret, end: caret });
  const range = lineRange(value, safe);
  const line = value.slice(range.start, range.end);
  const match = line.match(/^(\s*)([-*+]|\d+[.)])\s+(.*)$/);
  if (!match) return null;
  const [, indent, marker, content] = match;
  if (!content.trim() && safe.start >= range.start + line.length) {
    const next = `${value.slice(0, range.start)}${indent}${value.slice(range.end)}`;
    return editResult(next, range.start + indent.length);
  }
  const nextMarker = /^\d/.test(marker)
    ? `${Number.parseInt(marker, 10) + 1}.`
    : marker;
  const insertion = `\n${indent}${nextMarker} `;
  const next = `${value.slice(0, safe.start)}${insertion}${value.slice(safe.end)}`;
  return editResult(next, safe.start + insertion.length);
}

export function commandForMarkdownShortcut(event: MarkdownShortcutEvent): MarkdownCommand | null {
  if (event.isComposing || !(event.ctrlKey || event.metaKey)) return null;
  const key = event.key.toLowerCase();
  if (event.altKey && key === 't') return 'table';
  if (event.altKey) return null;
  if (key === 'b') return 'bold';
  if (key === 'i') return 'italic';
  if (key === 'k') return 'link';
  if (key === 'x' && event.shiftKey) return 'strike';
  if (!event.shiftKey && event.code === 'Backquote') return 'code';
  if (event.shiftKey && (event.code === 'Digit7' || key === '7' || key === '&')) return 'ordered-list';
  if (event.shiftKey && (event.code === 'Digit8' || key === '8' || key === '*')) return 'unordered-list';
  return null;
}

export function supportedImageMimeType(file: ImageFileLike): SupportedImageMimeType | null {
  const type = (file.type ?? '').split(';', 1)[0].trim().toLowerCase();
  if (SUPPORTED_IMAGE_MIME_SET.has(type)) return type as SupportedImageMimeType;
  if (type === 'image/jpg' || type === 'image/pjpeg') return 'image/jpeg';
  if (type && type !== 'application/octet-stream') return null;
  const name = (file.name ?? '').toLowerCase();
  const extension = name.slice(name.lastIndexOf('.'));
  return IMAGE_MIME_BY_EXTENSION[extension] ?? null;
}

export function normalizedImageFilename(
  file: ImageFileLike,
  mimeType: SupportedImageMimeType,
  fallback = 'pasted-image',
): string {
  const rawName = (file.name ?? '').trim().replace(/[\\/\0]/g, '-');
  const extension = IMAGE_EXTENSION_BY_MIME[mimeType];
  const withoutExtension = rawName.replace(/\.[^.]+$/, '') || fallback;
  const maxBaseLength = 255 - extension.length;
  return `${withoutExtension.slice(0, maxBaseLength)}${extension}`;
}
