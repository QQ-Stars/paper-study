/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import type { WorkspaceRouteHandle } from '../../app/routeHandle';
import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { WorkspaceRouteScaffold } from '../../components/workspace-shell/WorkspaceRouteScaffold';

export const handle = {
  title: '文献库',
  layout: 'inspector',
} satisfies WorkspaceRouteHandle;

export function Component() {
  return <WorkspaceRouteScaffold description="文献库正在连接真实论文数据。" />;
}

export const ErrorBoundary = RouteErrorBoundary;
