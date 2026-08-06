import { Marked } from 'marked';
import type { Token, TokenizerExtension, Tokens } from 'marked';

export type SafeNode =
  | { type: 'text'; value: string }
  | { type: 'paragraph'; children: SafeNode[] }
  | { type: 'link'; href: string; children: SafeNode[] }
  | { type: 'code'; value: string; language?: string }
  | { type: 'math'; value: string; display: boolean };

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
      return [text(code.text)];
    }
    case 'html': {
      const html = token as Tokens.HTML;
      return [text(html.raw)];
    }
    case 'br':
      return [text('\n')];
    case 'strong': {
      const strong = token as Tokens.Strong;
      return inlineTokens(strong.tokens);
    }
    case 'em': {
      const emphasis = token as Tokens.Em;
      return inlineTokens(emphasis.tokens);
    }
    case 'del': {
      const deletion = token as Tokens.Del;
      return inlineTokens(deletion.tokens);
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
  return tokens.flatMap(inlineToken);
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
  return paragraphTokenSegments(tokens).map((segment) => {
    if (segment.some((token) => token.type === 'html')) {
      return [text(segment.map((token) => token.raw).join(''))];
    }
    return inlineTokens(segment);
  });
}

function codeLanguage(token: Tokens.Code): string | undefined {
  const language = token.lang?.trim().split(/\s+/, 1)[0];
  return language && /^[A-Za-z0-9_+-]{1,32}$/.test(language)
    ? language.toLowerCase()
    : undefined;
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
    case 'paragraph': {
      const paragraph = token as Tokens.Paragraph;
      return paragraphSegments(paragraph.tokens).map((children) => ({ type: 'paragraph', children }));
    }
    case 'heading': {
      const heading = token as Tokens.Heading;
      return paragraphSegments(heading.tokens).map((children) => ({ type: 'paragraph', children }));
    }
    case 'html': {
      const html = token as Tokens.HTML;
      return [{ type: 'paragraph', children: [text(html.raw)] }];
    }
    case 'blockquote': {
      const blockquote = token as Tokens.Blockquote;
      return blockquote.tokens.flatMap(blockToken);
    }
    case 'list': {
      const list = token as Tokens.List;
      return list.items.flatMap((item, index) => {
        const marker = list.ordered ? `${Number(list.start || 1) + index}. ` : '• ';
        const children = item.tokens.flatMap(blockToken);
        const first = children[0];
        if (first?.type === 'paragraph') {
          return [{ ...first, children: [text(marker), ...first.children] }, ...children.slice(1)];
        }
        return [{ type: 'paragraph' as const, children: [text(marker), text(item.text)] }];
      });
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

    if (type === 'paragraph' || type === 'link') {
      const rawChildren = Reflect.get(input, 'children') as unknown;
      if (!Array.isArray(rawChildren)) throw new MarkdownProtocolError(`${path}.children must be an array`);
      const children = rawChildren.map((child, index) => decodeNode(child, `${path}.children[${index}]`, depth + 1, budget));
      if (type === 'paragraph') return { type, children };
      const rawHref = stringValue(Reflect.get(input, 'href'), `${path}.href`, 8_192);
      const href = safeAbsoluteUrl(rawHref);
      if (href === null) throw new MarkdownProtocolError(`${path}.href is not an allowed absolute URL`);
      return { type, href, children };
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
