import { describe, expect, it, vi } from 'vitest';

import { createApiClient } from '../../lib/api/client';
import { shouldRetryWorkspaceQuery } from './queryClient';

describe('workspace query retry policy', () => {
  it('does not retry a real transport error produced by a POST request', async () => {
    const client = createApiClient(vi.fn(async () => {
      throw new TypeError('offline');
    }));

    const error = await client.text('/api/paper/update', {
      method: 'POST',
    }).catch((caught: unknown) => caught);

    expect(error).toMatchObject({
      kind: 'network',
      requestMethod: 'POST',
    });
    expect(shouldRetryWorkspaceQuery(0, error)).toBe(false);
  });
});
