/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import { useEffect } from 'react';
import { useParams } from 'react-router-dom';

import type { WorkspaceRouteHandle } from '../../app/routeHandle';
import { useWorkspaceStore } from '../../app/stores/workspaceStore';
import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { WorkspaceRouteScaffold } from '../../components/workspace-shell/WorkspaceRouteScaffold';

export const handle = {
  title: '阅读',
  layout: 'reader-wide',
} satisfies WorkspaceRouteHandle;

export function Component() {
  const { paperId } = useParams<{ paperId: string }>();
  const setWorkspaceSelectionId = useWorkspaceStore(
    (state) => state.setWorkspaceSelectionId,
  );

  useEffect(() => {
    if (paperId) {
      setWorkspaceSelectionId(paperId);
    }
  }, [paperId, setWorkspaceSelectionId]);

  return (
    <WorkspaceRouteScaffold
      description="阅读器将只使用当前 URL 中的论文标识。"
      detail={paperId ?? '缺少论文标识'}
    />
  );
}

export const ErrorBoundary = RouteErrorBoundary;
