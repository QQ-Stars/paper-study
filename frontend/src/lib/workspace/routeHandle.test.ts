import { describe, expect, it } from 'vitest';

import { isWorkspaceRouteHandle } from './routeHandle';

describe('workspace route handles', () => {
  it('accepts a route-owned page header as an explicit layout contract', () => {
    expect(isWorkspaceRouteHandle({
      title: '研究概览',
      layout: 'inspector-timeline',
      pageHeader: 'route',
    })).toBe(true);
  });

  it('rejects unknown page-header ownership values', () => {
    expect(isWorkspaceRouteHandle({
      title: '研究概览',
      layout: 'inspector-timeline',
      pageHeader: 'floating',
    })).toBe(false);
  });
});
