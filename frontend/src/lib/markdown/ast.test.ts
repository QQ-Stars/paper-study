import { describe, expect, it } from 'vitest';

import {
  decodeSafeDocument,
  MarkdownProtocolError,
  parseMarkdown,
  UnsafeMarkdownInputError,
} from './ast';

describe('parseMarkdown', () => {
  it('keeps raw HTML and image alt text inert while allowing only absolute safe links', () => {
    const document = parseMarkdown([
      '<img src=x onerror=alert(1)>',
      '',
      '![diagram alt](https://images.example/diagram.png)',
      '',
      '[unsafe](javascript:alert(1)) [relative](./paper) [fragment](#note)',
      '',
      '[web](https://example.com/paper) [mail](mailto:reader@example.com)',
    ].join('\n'));

    expect(document).toEqual({
      version: 1,
      nodes: [
        {
          type: 'paragraph',
          children: [{ type: 'text', value: '<img src=x onerror=alert(1)>' }],
        },
        { type: 'paragraph', children: [{ type: 'text', value: 'diagram alt' }] },
        {
          type: 'paragraph',
          children: [
            { type: 'text', value: 'unsafe' },
            { type: 'text', value: ' ' },
            { type: 'text', value: 'relative' },
            { type: 'text', value: ' ' },
            { type: 'text', value: 'fragment' },
          ],
        },
        {
          type: 'paragraph',
          children: [
            { type: 'link', href: 'https://example.com/paper', children: [{ type: 'text', value: 'web' }] },
            { type: 'text', value: ' ' },
            { type: 'link', href: 'mailto:reader@example.com', children: [{ type: 'text', value: 'mail' }] },
          ],
        },
      ],
    });
  });

  it('extracts math only from ordinary Markdown text contexts', () => {
    const document = parseMarkdown([
      'Inline $x^2$ and \\(y + 1\\).',
      '',
      '$$\\frac{1}{2}$$',
      '',
      '`$code$`',
      '',
      '<span>$html$</span>',
      '',
      '![$image$](https://images.example/math.png)',
      '',
      'Unclosed $formula remains text.',
    ].join('\n'));

    expect(document).toEqual({
      version: 1,
      nodes: [
        {
          type: 'paragraph',
          children: [
            { type: 'text', value: 'Inline ' },
            { type: 'math', value: 'x^2', display: false },
            { type: 'text', value: ' and ' },
            { type: 'math', value: 'y + 1', display: false },
            { type: 'text', value: '.' },
          ],
        },
        { type: 'math', value: '\\frac{1}{2}', display: true },
        { type: 'paragraph', children: [{ type: 'inlineCode', value: '$code$' }] },
        { type: 'paragraph', children: [{ type: 'text', value: '<span>$html$</span>' }] },
        { type: 'paragraph', children: [{ type: 'text', value: '$image$' }] },
        { type: 'paragraph', children: [{ type: 'text', value: 'Unclosed $formula remains text.' }] },
      ],
    });
  });

  it('keeps paired raw HTML contents literal without flattening adjacent Markdown', () => {
    const document = parseMarkdown([
      '<span>$inside$</span> and **strong** with $outside$.',
      '',
      '<span data-value="$attribute">$ordinary$',
    ].join('\n'));

    expect(document).toEqual({
      version: 1,
      nodes: [
        {
          type: 'paragraph',
          children: [
            { type: 'text', value: '<span>$inside$</span>' },
            { type: 'text', value: ' and ' },
            { type: 'strong', children: [{ type: 'text', value: 'strong' }] },
            { type: 'text', value: ' with ' },
            { type: 'math', value: 'outside', display: false },
            { type: 'text', value: '.' },
          ],
        },
        {
          type: 'paragraph',
          children: [
            { type: 'text', value: '<span data-value="$attribute">' },
            { type: 'math', value: 'ordinary', display: false },
          ],
        },
      ],
    });
  });

  it('rejects pathological delimiter input before invoking Marked', () => {
    const pathological = '*_'.repeat(12_000);

    expect(() => parseMarkdown(pathological)).toThrow(UnsafeMarkdownInputError);
  });

  it('accepts only bounded structured-clone documents with safe link destinations', () => {
    const source = [
      '# **Paper**',
      '',
      '> Structured explanation with [paper](https://example.com/paper) and $x$.',
      '',
      '- [x] Parsed safely',
      '',
      '| Metric | Value |',
      '| --- | ---: |',
      '| ECE | `0.05` |',
    ].join('\n');
    const cloned = structuredClone(parseMarkdown(source));

    expect(decodeSafeDocument(cloned)).toEqual(cloned);
    expect(() => decodeSafeDocument({
      version: 1,
      nodes: [{
        type: 'link',
        href: 'javascript:alert(1)',
        children: [{ type: 'text', value: 'unsafe' }],
      }],
    })).toThrow(MarkdownProtocolError);

    const cyclic: { version: 1; nodes: unknown[] } = { version: 1, nodes: [] };
    cyclic.nodes.push(cyclic);
    expect(() => decodeSafeDocument(cyclic)).toThrow(MarkdownProtocolError);
  });
});
