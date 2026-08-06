import { describe, expect, it } from 'vitest';

import { processMarkdownWorkerRequest } from './markdown.worker';

describe('processMarkdownWorkerRequest', () => {
  it('returns a versioned structured document without an HTML payload', () => {
    const response = processMarkdownWorkerRequest({
      id: 4,
      generation: 9,
      source: '[paper](https://example.com/paper) and $x^2$',
    });

    expect(response).toEqual({
      id: 4,
      generation: 9,
      document: {
        version: 1,
        nodes: [{
          type: 'paragraph',
          children: [
            {
              type: 'link',
              href: 'https://example.com/paper',
              children: [{ type: 'text', value: 'paper' }],
            },
            { type: 'text', value: ' and ' },
            { type: 'math', value: 'x^2', display: false },
          ],
        }],
      },
    });
    expect(JSON.stringify(response)).not.toContain('html');
  });

  it('returns an identity-preserving error for pathological valid requests', () => {
    expect(processMarkdownWorkerRequest({
      id: 5,
      generation: 10,
      source: '*_'.repeat(12_000),
    })).toEqual({ id: 5, generation: 10, error: true });
  });

  it('ignores messages that do not match the request protocol', () => {
    expect(processMarkdownWorkerRequest({ id: 1, generation: 2 })).toBeNull();
    expect(processMarkdownWorkerRequest({ id: -1, generation: 2, source: 'text' })).toBeNull();
    expect(processMarkdownWorkerRequest(null)).toBeNull();
  });
});
