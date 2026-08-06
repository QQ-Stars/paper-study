import type { ComponentType } from 'react';

export type WorkspaceLayout =
  | 'standard'
  | 'inspector'
  | 'inspector-timeline'
  | 'reader-wide'
  | 'progress'
  | 'inspector-drawer';

const workspaceLayouts: ReadonlySet<string> = new Set<WorkspaceLayout>([
  'standard',
  'inspector',
  'inspector-timeline',
  'reader-wide',
  'progress',
  'inspector-drawer',
]);

export interface WorkspaceRouteHandle {
  title: string;
  layout: WorkspaceLayout;
  queue?: ComponentType;
  inspector?: ComponentType;
  timeline?: ComponentType;
}

export function isWorkspaceRouteHandle(
  value: unknown,
): value is WorkspaceRouteHandle {
  if (!value || typeof value !== 'object') return false;
  const candidate = value as Partial<WorkspaceRouteHandle>;
  return (
    typeof candidate.title === 'string'
    && typeof candidate.layout === 'string'
    && workspaceLayouts.has(candidate.layout)
  );
}
