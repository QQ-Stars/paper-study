/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import type { WorkspaceRouteHandle } from '../../app/routeHandle';
import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { WorkspaceRouteScaffold } from '../../components/workspace-shell/WorkspaceRouteScaffold';

export const handle = {
  title: '采集',
  layout: 'progress',
} satisfies WorkspaceRouteHandle;

export function Component() {
  return <WorkspaceRouteScaffold description="采集工作区正在等待研究命令。" />;
}

export const ErrorBoundary = RouteErrorBoundary;
