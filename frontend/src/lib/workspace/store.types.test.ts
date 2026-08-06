import { expect, it } from 'vitest';

import { resetWorkspaceStore, useWorkspaceStore } from './store';

it('keeps filter commands within their workspace surface domain', () => {
  resetWorkspaceStore();
  const setSurfaceFilters = useWorkspaceStore.getState().setSurfaceFilters;

  const invalidDashboardPatch = () => {
    // @ts-expect-error "added" belongs to the library surface.
    setSurfaceFilters('dashboard', { sort: 'added' });
  };
  const invalidLibraryPatch = () => {
    // @ts-expect-error "recent" belongs to the dashboard surface.
    setSurfaceFilters('library', { sort: 'recent' });
  };

  expect(invalidDashboardPatch).toEqual(expect.any(Function));
  expect(invalidLibraryPatch).toEqual(expect.any(Function));
  expect(useWorkspaceStore.getState().filters).toMatchObject({
    dashboard: { status: 'all', sort: 'recent' },
    library: { status: 'all', sort: 'added', source: 'all' },
  });
});
