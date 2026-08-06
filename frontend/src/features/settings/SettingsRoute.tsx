/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import type { WorkspaceRouteHandle } from '../../app/routeHandle';
import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { WorkspaceRouteScaffold } from '../../components/workspace-shell/WorkspaceRouteScaffold';

export const handle = {
  title: '设置',
  layout: 'standard',
} satisfies WorkspaceRouteHandle;

export function Component() {
  return <WorkspaceRouteScaffold description="设置视图正在连接脱敏配置。" />;
}

export const ErrorBoundary = RouteErrorBoundary;
