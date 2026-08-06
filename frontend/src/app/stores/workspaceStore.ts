import { create } from 'zustand';

export type WorkspaceSurface = 'dashboard' | 'library';
export type WorkspacePanel = 'command' | 'queue' | 'inspector';
export type WorkspaceDensity = 'compact' | 'comfortable';
export type WorkspaceTheme = 'dark';

export interface SurfaceFilters {
  query: string;
  status: string;
  sort: string;
}

interface WorkspacePanelState {
  active: WorkspacePanel | null;
  returnFocusId: string | null;
  restoreFocus: boolean;
}

interface WorkspaceState {
  workspaceSelectionId: string | null;
  filters: Record<WorkspaceSurface, SurfaceFilters>;
  panel: WorkspacePanelState;
  density: WorkspaceDensity;
  theme: WorkspaceTheme;
  setWorkspaceSelectionId: (paperId: string | null) => void;
  setSurfaceFilters: (
    surface: WorkspaceSurface,
    patch: Partial<SurfaceFilters>,
  ) => void;
  openPanel: (panel: WorkspacePanel, returnFocusId: string) => void;
  closePanel: () => void;
  dismissPanel: () => void;
  setDensity: (density: WorkspaceDensity) => void;
  setTheme: (theme: WorkspaceTheme) => void;
}

const createInitialState = () => ({
  workspaceSelectionId: null,
  filters: {
    dashboard: { query: '', status: 'all', sort: 'recent' },
    library: { query: '', status: 'all', sort: 'updated-desc' },
  },
  panel: { active: null, returnFocusId: null, restoreFocus: false },
  density: 'compact' as const,
  theme: 'dark' as const,
});

export const useWorkspaceStore = create<WorkspaceState>((set) => ({
  ...createInitialState(),
  setWorkspaceSelectionId: (workspaceSelectionId) =>
    set({ workspaceSelectionId }),
  setSurfaceFilters: (surface, patch) =>
    set((state) => ({
      filters: {
        ...state.filters,
        [surface]: { ...state.filters[surface], ...patch },
      },
    })),
  openPanel: (active, returnFocusId) =>
    set({ panel: { active, returnFocusId, restoreFocus: true } }),
  closePanel: () =>
    set((state) => ({
      panel: { ...state.panel, active: null },
    })),
  dismissPanel: () =>
    set({
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
