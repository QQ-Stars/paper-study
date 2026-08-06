import { create } from 'zustand';

import type { StudyStatus } from '../api/types';

export type WorkspaceSurface = 'dashboard' | 'library';
export type WorkspacePanel = 'command' | 'queue' | 'inspector';
export type WorkspaceDensity = 'compact' | 'comfortable';
export type WorkspaceTheme = 'dark';

export type WorkspaceStudyStatusFilter = 'all' | StudyStatus;
export type DashboardSort = 'recent' | 'title' | 'year' | 'relevance';
export type LibrarySourceFilter = 'all' | 'seed' | 'collected';
export type LibrarySort = 'added' | 'relevance' | 'year' | 'citations' | 'title';

export interface DashboardSurfaceFilters {
  query: string;
  status: WorkspaceStudyStatusFilter;
  sort: DashboardSort;
}

export interface LibrarySurfaceFilters {
  query: string;
  status: WorkspaceStudyStatusFilter;
  sort: LibrarySort;
  venue: string;
  type: string;
  topic: string;
  year: string;
  source: LibrarySourceFilter;
  favorite: boolean;
}

interface WorkspaceSurfaceFilterMap {
  dashboard: DashboardSurfaceFilters;
  library: LibrarySurfaceFilters;
}

interface WorkspacePanelState {
  active: WorkspacePanel | null;
  returnFocusId: string | null;
  restoreFocus: boolean;
}

interface WorkspaceState {
  workspaceSelectionId: string | null;
  filters: WorkspaceSurfaceFilterMap;
  panel: WorkspacePanelState;
  density: WorkspaceDensity;
  theme: WorkspaceTheme;
  setWorkspaceSelectionId: (paperId: string | null) => void;
  setSurfaceFilters: <Surface extends WorkspaceSurface>(
    surface: Surface,
    patch: Partial<WorkspaceSurfaceFilterMap[Surface]>,
  ) => void;
  openPanel: (panel: WorkspacePanel, returnFocusId: string) => void;
  closePanel: () => void;
  dismissPanel: () => void;
  setDensity: (density: WorkspaceDensity) => void;
  setTheme: (theme: WorkspaceTheme) => void;
}

type WorkspaceData = Pick<
  WorkspaceState,
  'workspaceSelectionId' | 'filters' | 'panel' | 'density' | 'theme'
>;

const createInitialState = (): WorkspaceData => ({
  workspaceSelectionId: null,
  filters: {
    dashboard: { query: '', status: 'all', sort: 'recent' },
    library: {
      query: '',
      status: 'all',
      sort: 'added',
      venue: 'all',
      type: 'all',
      topic: 'all',
      year: 'all',
      source: 'all',
      favorite: false,
    },
  },
  panel: { active: null, returnFocusId: null, restoreFocus: false },
  density: 'compact',
  theme: 'dark',
});

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  ...createInitialState(),
  setWorkspaceSelectionId: (workspaceSelectionId) => set({ workspaceSelectionId }),
  setSurfaceFilters: (surface, patch) => set((state) => ({
    filters: {
      ...state.filters,
      [surface]: { ...state.filters[surface], ...patch },
    } as WorkspaceSurfaceFilterMap,
  })),
  openPanel: (active, returnFocusId) => set({
    panel: { active, returnFocusId, restoreFocus: true },
  }),
  closePanel: () => set((state) => ({
    panel: { ...state.panel, active: null },
  })),
  dismissPanel: () => set({
    panel: {
      active: null,
      returnFocusId: null,
      restoreFocus: false,
    },
  }),
  setDensity: (density) => set({ density }),
  setTheme: (theme) => set({ theme }),
}));

export function resetWorkspaceStore(): void {
  useWorkspaceStore.setState(createInitialState());
}
