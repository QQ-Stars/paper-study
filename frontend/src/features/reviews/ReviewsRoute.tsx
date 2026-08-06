/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import type { WorkspaceRouteHandle } from '../../app/routeHandle';
import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { WorkspaceRouteScaffold } from '../../components/workspace-shell/WorkspaceRouteScaffold';

export const handle = {
  title: '复习',
  layout: 'standard',
} satisfies WorkspaceRouteHandle;

export function Component() {
  return <WorkspaceRouteScaffold description="复习队列正在连接权威计划快照。" />;
}

export const ErrorBoundary = RouteErrorBoundary;
