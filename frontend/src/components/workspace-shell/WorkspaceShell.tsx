import { useEffect, useMemo, useRef } from 'react';
import { Link, Outlet, useMatches } from 'react-router-dom';

import {
  isWorkspaceRouteHandle,
  type WorkspaceRouteHandle,
} from '../../app/routeHandle';
import { useWorkspaceStore } from '../../app/stores/workspaceStore';
import {
  focusMainContent,
  focusPageTitle,
} from '../../lib/accessibility/focus';
import { CommandBar, CommandPanel } from '../command-bar/CommandBar';
import { LiveAnnouncer } from '../feedback/LiveAnnouncer';
import { WorkspaceSlotBoundary } from '../feedback/WorkspaceSlotBoundary';
import { GlobalNavigation } from '../navigation/GlobalNavigation';
import { ResponsivePanelHost } from '../overlays/ResponsivePanelHost';

const fallbackHandle: WorkspaceRouteHandle = {
  title: '研究工作区',
  layout: 'standard',
};

export function WorkspaceShell() {
  const matches = useMatches();
  const activePanel = useWorkspaceStore((state) => state.panel.active);
  const density = useWorkspaceStore((state) => state.density);
  const theme = useWorkspaceStore((state) => state.theme);
  const previousRouteIdRef = useRef<string | null>(null);

  const { handle, routeId } = useMemo(() => {
    const currentMatch = [...matches]
      .reverse()
      .find((match) => isWorkspaceRouteHandle(match.handle));

    return {
      handle: isWorkspaceRouteHandle(currentMatch?.handle)
        ? currentMatch.handle
        : fallbackHandle,
      routeId: currentMatch?.id ?? null,
    };
  }, [matches]);

  const Inspector = handle.inspector;
  const Timeline = handle.timeline;

  useEffect(() => {
    document.title = `${handle.title} | Paper Study`;

    if (
      previousRouteIdRef.current !== null &&
      previousRouteIdRef.current !== routeId
    ) {
      focusPageTitle();
    }
    previousRouteIdRef.current = routeId;
  }, [handle.title, routeId]);

  useEffect(() => {
    const root = document.documentElement;
    const previousTheme = root.getAttribute('data-theme');
    const previousDensity = root.getAttribute('data-density');
    root.dataset.theme = theme;
    root.dataset.density = density;

    return () => {
      if (previousTheme === null) {
        root.removeAttribute('data-theme');
      } else {
        root.setAttribute('data-theme', previousTheme);
      }
      if (previousDensity === null) {
        root.removeAttribute('data-density');
      } else {
        root.setAttribute('data-density', previousDensity);
      }
    };
  }, [density, theme]);

  return (
    <div
      className="workspace-shell"
      role="application"
      aria-label="Paper Study 研究工作区"
      data-layout={handle.layout}
      data-panel={activePanel ?? 'closed'}
    >
      <a
        className="skip-link"
        href="#workspace-main"
        onClick={(event) => {
          event.preventDefault();
          focusMainContent();
        }}
      >
        跳到主要内容
      </a>

      <GlobalNavigation />

      <div className="workspace-shell__body">
        <CommandBar />

        <header className="workspace-page-header">
          <nav aria-label="面包屑">
            <Link to="/dashboard">研究工作区</Link>
            <span aria-hidden="true">/</span>
            <span aria-current="page">{handle.title}</span>
          </nav>
          <h1 id="workspace-page-title" tabIndex={-1}>
            {handle.title}
          </h1>
        </header>

        <div className="workspace-shell__content">
          <main id="workspace-main" tabIndex={-1}>
            <Outlet />
          </main>
          <ResponsivePanelHost
            command={<CommandPanel />}
            inspector={Inspector ? (
              <WorkspaceSlotBoundary
                key={`${routeId ?? 'fallback'}:inspector`}
                label="论文上下文"
              >
                <Inspector />
              </WorkspaceSlotBoundary>
            ) : undefined}
          />
        </div>

        {Timeline ? (
          <section className="workspace-timeline" aria-label="研究时间线">
            <WorkspaceSlotBoundary
              key={`${routeId ?? 'fallback'}:timeline`}
              label="研究时间线"
            >
              <Timeline />
            </WorkspaceSlotBoundary>
          </section>
        ) : null}
      </div>

      <LiveAnnouncer />
    </div>
  );
}
