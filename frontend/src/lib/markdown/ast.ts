import { Marked } from 'marked';
import type { Token, TokenizerExtension, Tokens } from 'marked';

export type SafeAlignment = 'left' | 'center' | 'right' | null;

export type SafeNode =
  | { type: 'text'; value: string }
  | { type: 'paragraph'; children: SafeNode[] }
  | { type: 'heading'; depth: 1 | 2 | 3 | 4 | 5 | 6; children: SafeNode[] }
  | { type: 'strong'; children: SafeNode[] }
  | { type: 'emphasis'; children: SafeNode[] }
  | { type: 'deletion'; children: SafeNode[] }
  | { type: 'link'; href: string; children: SafeNode[] }
  | { type: 'inlineCode'; value: string }
  | { type: 'code'; value: string; language?: string }
  | { type: 'math'; value: string; display: boolean }
  | { type: 'lineBreak' }
  | { type: 'thematicBreak' }
  | { type: 'blockquote'; children: SafeNode[] }
  | { type: 'list'; ordered: boolean; start?: number; children: SafeNode[] }
  | { type: 'listItem'; checked?: boolean; children: SafeNode[] }
  | { type: 'table'; children: SafeNode[] }
  | { type: 'tableRow'; children: SafeNode[] }
  | { type: 'tableCell'; header: boolean; align: SafeAlignment; children: SafeNode[] };

export interface SafeDocument {
  version: 1;
  nodes: SafeNode[];
}

const blankLine = /(?:\r?\n){2,}/;
const maximumSourceLength = 200_000;
const maximumFormattingDelimiters = 10_000;

export class UnsafeMarkdownInputError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'UnsafeMarkdownInputError';
  }
}

export class MarkdownProtocolError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'MarkdownProtocolError';
  }
}

interface MathToken extends Tokens.Generic {
  type: 'safeMath';
  text: string;
  display: boolean;
}

function mathExtension(
  level: 'block' | 'inline',
  patterns: readonly RegExp[],
  display: boolean,
): TokenizerExtension {
  return {
    name: display ? 'safeBlockMath' : 'safeInlineMath',
    level,
    start(source) {
      const positions = patterns
        .map((pattern) => source.search(new RegExp(pattern.source.replace(/^\^/, ''), pattern.flags)))
        .filter((position) => position >= 0);
      return positions.length ? Math.min(...positions) : undefined;
    },
    tokenizer(source) {
      for (const pattern of patterns) {
        const match = pattern.exec(source);
        if (!match?.[0] || !match[1]?.trim()) continue;
        const token: MathToken = {
          type: 'safeMath',
          raw: match[0],
          text: match[1].trim(),
          display,
        };
        return token;
      }
      return undefined;
    },
  };
}

const markdown = new Marked({ gfm: true, breaks: false });
markdown.use({
  extensions: [
    mathExtension('block', [
      /^\$\$[ \t]*(?:\r?\n)?([\s\S]+?)(?:\r?\n)?[ \t]*\$\$(?:\r?\n+|$)/,
      /^\\\[[ \t]*(?:\r?\n)?([\s\S]+?)(?:\r?\n)?[ \t]*\\\](?:\r?\n+|$)/,
    ], true),
    mathExtension('inline', [
      /^\$([^$\r\n\s](?:\\.|[^$\r\n])*?[^$\r\n\s]|[^$\r\n\s])\$(?!\$)/,
      /^\\\(([\s\S]+?)\\\)/,
    ], false),
  ],
});

function text(value: string): SafeNode {
  return { type: 'text', value };
}

function childrenOf(token: Token): Token[] {
  const children = Reflect.get(token, 'tokens') as unknown;
  return Array.isArray(children) ? children as Token[] : [];
}

function hasAsciiControlCharacter(value: string): boolean {
  for (let index = 0; index < value.length; index += 1) {
    const code = value.charCodeAt(index);
    if (code <= 31 || code === 127) return true;
  }
  return false;
}

export function safeAbsoluteUrl(value: string): string | null {
  const href = value.trim();
  if (!href || hasAsciiControlCharacter(href)) return null;
  try {
    const url = new URL(href);
    if (url.protocol !== 'http:' && url.protocol !== 'https:' && url.protocol !== 'mailto:') {
      return null;
    }
    return url.href;
  } catch {
    return null;
  }
}

const voidHtmlElements = new Set([
  'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
  'link', 'meta', 'param', 'source', 'track', 'wbr',
]);

interface HtmlTagBoundary {
  tag: string;
  closing: boolean;
  selfClosing: boolean;
}

function htmlTagBoundary(token: Token): HtmlTagBoundary | null {
  if (token.type !== 'html') return null;
  const raw = token.raw.trim();
  const match = raw.match(/^<\s*(\/?)\s*([A-Za-z][\w:-]*)\b[^>]*>$/);
  if (!match) return null;
  const tag = match[2].toLowerCase();
  const closing = match[1] === '/';
  return {
    tag,
    closing,
    selfClosing: !closing && (voidHtmlElements.has(tag) || /\/\s*>$/.test(raw)),
  };
}

function pairedHtmlDepthChanges(tokens: Token[]): {
  enter: Map<number, number>;
  exit: Map<number, number>;
} {
  const stack: Array<{ tag: string; index: number }> = [];
  const pairs: Array<{ start: number; end: number }> = [];

  tokens.forEach((token, index) => {
    const boundary = htmlTagBoundary(token);
    if (boundary === null) return;
    if (!boundary.closing) {
      if (boundary.selfClosing) return;
      stack.push({ tag: boundary.tag, index });
      return;
    }

    let openerIndex = -1;
    for (let stackIndex = stack.length - 1; stackIndex >= 0; stackIndex -= 1) {
      if (stack[stackIndex]?.tag === boundary.tag) {
        openerIndex = stackIndex;
        break;
      }
    }
    if (openerIndex < 0) return;
    const [opener] = stack.splice(openerIndex);
    if (opener && index > opener.index) pairs.push({ start: opener.index, end: index });
  });

  const enter = new Map<number, number>();
  const exit = new Map<number, number>();
  pairs.forEach(({ start, end }) => {
    enter.set(start, (enter.get(start) ?? 0) + 1);
    exit.set(end, (exit.get(end) ?? 0) + 1);
  });
  return { enter, exit };
}

function inlineToken(token: Token): SafeNode[] {
  switch (token.type) {
    case 'safeMath': {
      const math = token as MathToken;
      return [{ type: 'math', value: math.text, display: math.display }];
    }
    case 'image': {
      const image = token as Tokens.Image;
      return [text(image.text)];
    }
    case 'link': {
      const link = token as Tokens.Link;
      const children = inlineTokens(link.tokens);
      const href = safeAbsoluteUrl(link.href);
      return href === null ? children : [{ type: 'link', href, children }];
    }
    case 'codespan': {
      const code = token as Tokens.Codespan;
      return [{ type: 'inlineCode', value: code.text }];
    }
    case 'html': {
      const html = token as Tokens.HTML;
      return [text(html.raw)];
    }
    case 'br':
      return [{ type: 'lineBreak' }];
    case 'strong': {
      const strong = token as Tokens.Strong;
      return [{ type: 'strong', children: inlineTokens(strong.tokens) }];
    }
    case 'em': {
      const emphasis = token as Tokens.Em;
      return [{ type: 'emphasis', children: inlineTokens(emphasis.tokens) }];
    }
    case 'del': {
      const deletion = token as Tokens.Del;
      return [{ type: 'deletion', children: inlineTokens(deletion.tokens) }];
    }
    case 'escape': {
      const escape = token as Tokens.Escape;
      return [text(escape.text)];
    }
    case 'text': {
      const value = token as Tokens.Text;
      return value.tokens?.length ? inlineTokens(value.tokens) : [text(value.text)];
    }
    default:
      if (childrenOf(token).length) return inlineTokens(childrenOf(token));
      return token.raw ? [text(token.raw)] : [];
  }
}

function inlineTokens(tokens: Token[]): SafeNode[] {
  const { enter, exit } = pairedHtmlDepthChanges(tokens);
  const nodes: SafeNode[] = [];
  let rawHtmlDepth = 0;
  let continuingRawHtml = false;

  tokens.forEach((token, index) => {
    const exiting = exit.get(index) ?? 0;
    const entering = enter.get(index) ?? 0;
    rawHtmlDepth = Math.max(0, rawHtmlDepth - exiting);
    const inPairedRawHtml = rawHtmlDepth > 0 || entering > 0 || exiting > 0;

    if (inPairedRawHtml) {
      const previous = nodes.at(-1);
      if (continuingRawHtml && previous?.type === 'text') previous.value += token.raw;
      else nodes.push(text(token.raw));
      continuingRawHtml = true;
    } else {
      continuingRawHtml = false;
      nodes.push(...inlineToken(token));
    }
    rawHtmlDepth += entering;
  });

  return nodes;
}

function paragraphTokenSegments(tokens: Token[]): Token[][] {
  const segments: Token[][] = [[]];
  for (const token of tokens) {
    if (token.type !== 'text') {
      segments.at(-1)?.push(token);
      continue;
    }

    const value = token as Tokens.Text;
    if (!blankLine.test(value.text)) {
      segments.at(-1)?.push(token);
      continue;
    }

    const parts = value.text.split(blankLine);
    parts.forEach((part, index) => {
      if (part) {
        segments.at(-1)?.push({
          type: 'text',
          raw: part,
          text: part,
        } satisfies Tokens.Text);
      }
      if (index < parts.length - 1) segments.push([]);
    });
  }
  return segments.filter((segment) => segment.length > 0);
}

function paragraphSegments(tokens: Token[]): SafeNode[][] {
  return paragraphTokenSegments(tokens).map(inlineTokens);
}

function codeLanguage(token: Tokens.Code): string | undefined {
  const language = token.lang?.trim().split(/\s+/, 1)[0];
  return language && /^[A-Za-z0-9_+-]{1,32}$/.test(language)
    ? language.toLowerCase()
    : undefined;
}

function headingDepth(value: number): 1 | 2 | 3 | 4 | 5 | 6 {
  if (value >= 1 && value <= 6) return value as 1 | 2 | 3 | 4 | 5 | 6;
  return 6;
}

function tableCell(cell: Tokens.TableCell): SafeNode {
  return {
    type: 'tableCell',
    header: cell.header,
    align: cell.align,
    children: inlineTokens(cell.tokens),
  };
}

function blockToken(token: Token): SafeNode[] {
  switch (token.type) {
    case 'space':
    case 'def':
      return [];
    case 'code': {
      const code = token as Tokens.Code;
      const language = codeLanguage(code);
      return language === undefined
        ? [{ type: 'code', value: code.text }]
        : [{ type: 'code', value: code.text, language }];
    }
    case 'safeMath': {
      const math = token as MathToken;
      return [{ type: 'math', value: math.text, display: math.display }];
    }
    case 'hr':
      return [{ type: 'thematicBreak' }];
    case 'paragraph': {
      const paragraph = token as Tokens.Paragraph;
      return paragraphSegments(paragraph.tokens).map((children) => ({ type: 'paragraph', children }));
    }
    case 'heading': {
      const heading = token as Tokens.Heading;
      const depth = headingDepth(heading.depth);
      return paragraphSegments(heading.tokens).map((children) => ({ type: 'heading', depth, children }));
    }
    case 'html': {
      const html = token as Tokens.HTML;
      return [{ type: 'paragraph', children: [text(html.raw)] }];
    }
    case 'blockquote': {
      const blockquote = token as Tokens.Blockquote;
      return [{ type: 'blockquote', children: blockquote.tokens.flatMap(blockToken) }];
    }
    case 'list': {
      const list = token as Tokens.List;
      const children: SafeNode[] = list.items.map((item) => ({
        type: 'listItem',
        ...(item.task && typeof item.checked === 'boolean' ? { checked: item.checked } : {}),
        children: item.tokens.flatMap(blockToken),
      }));
      const start = list.ordered && typeof list.start === 'number'
        ? Math.max(0, Math.trunc(list.start))
        : undefined;
      return [{
        type: 'list',
        ordered: list.ordered,
        ...(start === undefined ? {} : { start }),
        children,
      }];
    }
    case 'table': {
      const table = token as Tokens.Table;
      const header: SafeNode = {
        type: 'tableRow',
        children: table.header.map(tableCell),
      };
      const rows: SafeNode[] = table.rows.map((row) => ({
        type: 'tableRow',
        children: row.map(tableCell),
      }));
      return [{ type: 'table', children: [header, ...rows] }];
    }
    case 'text': {
      const value = token as Tokens.Text;
      return [{ type: 'paragraph', children: inlineTokens(value.tokens ?? [value]) }];
    }
    default:
      if (childrenOf(token).length) return childrenOf(token).flatMap(blockToken);
      return token.raw ? [{ type: 'paragraph', children: [text(token.raw)] }] : [];
  }
}

export function plainTextDocument(source: string): SafeDocument {
  return { version: 1, nodes: source ? [text(source)] : [] };
}

function objectValue(value: unknown, path: string): object {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new MarkdownProtocolError(`${path} must be an object`);
  }
  return value;
}

function stringValue(value: unknown, path: string, maximum = maximumSourceLength): string {
  if (typeof value !== 'string' || value.length > maximum) {
    throw new MarkdownProtocolError(`${path} must be a bounded string`);
  }
  return value;
}

interface DecodeBudget {
  nodes: number;
  characters: number;
  active: WeakSet<object>;
}

function decodeChildren(
  input: object,
  path: string,
  depth: number,
  budget: DecodeBudget,
): SafeNode[] {
  const rawChildren = Reflect.get(input, 'children') as unknown;
  if (!Array.isArray(rawChildren)) throw new MarkdownProtocolError(`${path}.children must be an array`);
  return rawChildren.map((child, index) => (
    decodeNode(child, `${path}.children[${index}]`, depth + 1, budget)
  ));
}

function decodeNode(value: unknown, path: string, depth: number, budget: DecodeBudget): SafeNode {
  if (depth > 32) throw new MarkdownProtocolError(`${path} exceeds the maximum AST depth`);
  const input = objectValue(value, path);
  if (budget.active.has(input)) throw new MarkdownProtocolError(`${path} contains a cycle`);
  budget.active.add(input);
  budget.nodes += 1;
  if (budget.nodes > 20_000) throw new MarkdownProtocolError('Markdown AST contains too many nodes');

  try {
    const type = Reflect.get(input, 'type');
    if (type === 'text') {
      const valueText = stringValue(Reflect.get(input, 'value'), `${path}.value`);
      budget.characters += valueText.length;
      if (budget.characters > 500_000) throw new MarkdownProtocolError('Markdown AST contains too much text');
      return { type, value: valueText };
    }

    if (
      type === 'paragraph'
      || type === 'strong'
      || type === 'emphasis'
      || type === 'deletion'
      || type === 'blockquote'
      || type === 'table'
      || type === 'tableRow'
    ) {
      return { type, children: decodeChildren(input, path, depth, budget) };
    }

    if (type === 'heading') {
      const rawDepth = Reflect.get(input, 'depth') as unknown;
      if (!Number.isInteger(rawDepth) || (rawDepth as number) < 1 || (rawDepth as number) > 6) {
        throw new MarkdownProtocolError(`${path}.depth must be an integer from 1 to 6`);
      }
      return {
        type,
        depth: rawDepth as 1 | 2 | 3 | 4 | 5 | 6,
        children: decodeChildren(input, path, depth, budget),
      };
    }

    if (type === 'link') {
      const children = decodeChildren(input, path, depth, budget);
      const rawHref = stringValue(Reflect.get(input, 'href'), `${path}.href`, 8_192);
      const href = safeAbsoluteUrl(rawHref);
      if (href === null) throw new MarkdownProtocolError(`${path}.href is not an allowed absolute URL`);
      return { type, href, children };
    }

    if (type === 'list') {
      const ordered = Reflect.get(input, 'ordered') as unknown;
      if (typeof ordered !== 'boolean') throw new MarkdownProtocolError(`${path}.ordered must be boolean`);
      const children = decodeChildren(input, path, depth, budget);
      const rawStart = Reflect.get(input, 'start') as unknown;
      if (rawStart === undefined) return { type, ordered, children };
      if (!Number.isSafeInteger(rawStart) || (rawStart as number) < 0 || (rawStart as number) > 1_000_000) {
        throw new MarkdownProtocolError(`${path}.start must be a bounded non-negative integer`);
      }
      return { type, ordered, start: rawStart as number, children };
    }

    if (type === 'listItem') {
      const children = decodeChildren(input, path, depth, budget);
      const checked = Reflect.get(input, 'checked') as unknown;
      if (checked === undefined) return { type, children };
      if (typeof checked !== 'boolean') throw new MarkdownProtocolError(`${path}.checked must be boolean`);
      return { type, checked, children };
    }

    if (type === 'tableCell') {
      const header = Reflect.get(input, 'header') as unknown;
      const align = Reflect.get(input, 'align') as unknown;
      if (typeof header !== 'boolean') throw new MarkdownProtocolError(`${path}.header must be boolean`);
      if (align !== null && align !== 'left' && align !== 'center' && align !== 'right') {
        throw new MarkdownProtocolError(`${path}.align is unsupported`);
      }
      return {
        type,
        header,
        align: align as SafeAlignment,
        children: decodeChildren(input, path, depth, budget),
      };
    }

    if (type === 'lineBreak' || type === 'thematicBreak') return { type };

    if (type === 'inlineCode') {
      const code = stringValue(Reflect.get(input, 'value'), `${path}.value`);
      budget.characters += code.length;
      if (budget.characters > 500_000) throw new MarkdownProtocolError('Markdown AST contains too much text');
      return { type, value: code };
    }

    if (type === 'code') {
      const code = stringValue(Reflect.get(input, 'value'), `${path}.value`);
      budget.characters += code.length;
      if (budget.characters > 500_000) throw new MarkdownProtocolError('Markdown AST contains too much text');
      const rawLanguage = Reflect.get(input, 'language') as unknown;
      if (rawLanguage === undefined) return { type, value: code };
      const language = stringValue(rawLanguage, `${path}.language`, 32);
      if (!/^[a-z0-9_+-]{1,32}$/.test(language)) {
        throw new MarkdownProtocolError(`${path}.language is invalid`);
      }
      return { type, value: code, language };
    }

    if (type === 'math') {
      const formula = stringValue(Reflect.get(input, 'value'), `${path}.value`);
      const display = Reflect.get(input, 'display') as unknown;
      if (typeof display !== 'boolean') throw new MarkdownProtocolError(`${path}.display must be boolean`);
      budget.characters += formula.length;
      if (budget.characters > 500_000) throw new MarkdownProtocolError('Markdown AST contains too much text');
      return { type, value: formula, display };
    }

    throw new MarkdownProtocolError(`${path}.type is unsupported`);
  } finally {
    budget.active.delete(input);
  }
}

export function decodeSafeDocument(value: unknown): SafeDocument {
  const input = objectValue(value, '$');
  if (Reflect.get(input, 'version') !== 1) {
    throw new MarkdownProtocolError('$.version must be 1');
  }
  const rawNodes = Reflect.get(input, 'nodes') as unknown;
  if (!Array.isArray(rawNodes)) throw new MarkdownProtocolError('$.nodes must be an array');
  const budget: DecodeBudget = { nodes: 0, characters: 0, active: new WeakSet<object>() };
  return {
    version: 1,
    nodes: rawNodes.map((node, index) => decodeNode(node, `$.nodes[${index}]`, 0, budget)),
  };
}

export function parseMarkdown(source: string): SafeDocument {
  const input = String(source);
  if (input.length > maximumSourceLength) {
    throw new UnsafeMarkdownInputError('Markdown source exceeds the safe parsing limit');
  }
  let formattingDelimiters = 0;
  for (const character of input) {
    if (character === '*' || character === '_') formattingDelimiters += 1;
    if (formattingDelimiters > maximumFormattingDelimiters) {
      throw new UnsafeMarkdownInputError('Markdown contains a pathological number of formatting delimiters');
    }
  }
  const tokens = markdown.lexer(input);
  return { version: 1, nodes: tokens.flatMap(blockToken) };
}
