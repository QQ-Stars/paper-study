/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import type { WorkspaceRouteHandle } from '../../app/routeHandle';
import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { WorkspaceRouteScaffold } from '../../components/workspace-shell/WorkspaceRouteScaffold';

export const handle = {
  title: '洞察',
  layout: 'standard',
} satisfies WorkspaceRouteHandle;

export function Component() {
  return <WorkspaceRouteScaffold description="洞察视图只会呈现真实研究数据。" />;
}

export const ErrorBoundary = RouteErrorBoundary;
