/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import { useParams } from 'react-router-dom';

import type { WorkspaceRouteHandle } from '../../app/routeHandle';
import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { WorkspaceRouteScaffold } from '../../components/workspace-shell/WorkspaceRouteScaffold';

export const handle = {
  title: '任务',
  layout: 'inspector-drawer',
} satisfies WorkspaceRouteHandle;

export function Component() {
  const { jobId } = useParams<{ jobId: string }>();
  return (
    <WorkspaceRouteScaffold
      description="后台任务工作区正在连接真实任务状态。"
      detail={jobId}
    />
  );
}

export const ErrorBoundary = RouteErrorBoundary;
