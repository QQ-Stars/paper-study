import {
  createBrowserRouter,
  createMemoryRouter,
  Navigate,
  type InitialEntry,
  type RouteObject,
} from 'react-router-dom';

import { RouteErrorBoundary } from '../components/feedback/RouteErrorBoundary';
import { WorkspaceHydrationFallback } from '../components/feedback/WorkspaceHydrationFallback';
import { WorkspaceShell } from '../components/workspace-shell/WorkspaceShell';
import { WorkspaceNotFoundRoute } from '../components/workspace-shell/WorkspaceRouteScaffold';
import type { WorkspaceRouteHandle } from '../lib/workspace';

const notFoundHandle = {
  title: '页面不存在',
  layout: 'standard',
} satisfies WorkspaceRouteHandle;

export function createWorkspaceRoutes(): RouteObject[] {
  return [
    {
      id: 'workspace-shell',
      path: '/',
      Component: WorkspaceShell,
      ErrorBoundary: RouteErrorBoundary,
      HydrateFallback: WorkspaceHydrationFallback,
      children: [
        {
          index: true,
          element: <Navigate to="/dashboard" replace />,
        },
        {
          id: 'dashboard',
          path: 'dashboard',
          lazy: () => import('../features/dashboard'),
        },
        {
          id: 'library',
          path: 'library',
          lazy: () => import('../features/library'),
        },
        {
          id: 'reader',
          path: 'reader/:paperId',
          lazy: () => import('../features/reader'),
        },
        {
          id: 'reviews',
          path: 'reviews',
          lazy: () => import('../features/reviews'),
        },
        {
          id: 'acquire',
          path: 'acquire',
          lazy: () => import('../features/acquire'),
        },
        {
          id: 'jobs',
          path: 'jobs/:jobId?',
          lazy: () => import('../features/jobs'),
        },
        {
          id: 'insights',
          path: 'insights',
          lazy: () => import('../features/insights'),
        },
        {
          id: 'settings',
          path: 'settings',
          lazy: () => import('../features/settings'),
        },
        {
          id: 'not-found',
          path: '*',
          Component: WorkspaceNotFoundRoute,
          handle: notFoundHandle,
        },
      ],
    },
  ];
}

export function createWorkspaceBrowserRouter() {
  return createBrowserRouter(createWorkspaceRoutes(), {
    basename: '/workspace',
  });
}

export function createWorkspaceMemoryRouter(
  initialEntries: InitialEntry[] = ['/dashboard'],
) {
  return createMemoryRouter(createWorkspaceRoutes(), { initialEntries });
}
