import {
  useEffect,
  useRef,
  type ReactNode,
} from 'react';

import {
  type WorkspacePanel,
  useWorkspaceStore,
} from '../../lib/workspace';
import {
  focusFirstWithin,
  restoreFocus,
  trapTabKey,
} from '../../lib/accessibility/focus';
import { useWorkspaceMediaQuery } from '../../lib/accessibility/media';

const panelCopy: Record<WorkspacePanel, { title: string; description: string }> = {
  command: {
    title: '命令栏',
    description: '搜索论文、切换视图或运行研究命令。',
  },
  queue: {
    title: '研究队列',
    description: '当前队列会在对应功能接入后显示真实任务。',
  },
  inspector: {
    title: '论文上下文',
    description: '选择论文后，这里会显示该论文的真实上下文。',
  },
};

interface ResponsivePanelHostProps {
  inspector?: ReactNode;
  queue?: ReactNode;
  command?: ReactNode;
}

export function ResponsivePanelHost({
  inspector,
  queue,
  command,
}: ResponsivePanelHostProps) {
  const activePanel = useWorkspaceStore((state) => state.panel.active);
  const returnFocusId = useWorkspaceStore(
    (state) => state.panel.returnFocusId,
  );
  const restoreFocusOnClose = useWorkspaceStore(
    (state) => state.panel.restoreFocus,
  );
  const closePanel = useWorkspaceStore((state) => state.closePanel);
  const isOverlay = useWorkspaceMediaQuery('(max-width: 1099px)');
  const isMobile = useWorkspaceMediaQuery('(max-width: 760px)');
  const reducedMotion = useWorkspaceMediaQuery(
    '(prefers-reduced-motion: reduce)',
  );
  const reducedTransparency = useWorkspaceMediaQuery(
    '(prefers-reduced-transparency: reduce)',
  );
  const dialogRef = useRef<HTMLElement>(null);
  const previousPanelRef = useRef<WorkspacePanel | null>(null);
  const latestTriggerRef = useRef<string | null>(null);
  const previousOverlayRef = useRef(isOverlay);
  const showModal =
    activePanel !== null && (isOverlay || activePanel !== 'inspector');

  useEffect(() => {
    let cancelled = false;
    const previousPanel = previousPanelRef.current;

    if (showModal) {
      latestTriggerRef.current = returnFocusId;
      queueMicrotask(() => {
        if (!cancelled && dialogRef.current) {
          focusFirstWithin(dialogRef.current);
        }
      });
    } else if (previousPanel && !activePanel) {
      const triggerId = latestTriggerRef.current;
      latestTriggerRef.current = null;
      if (restoreFocusOnClose && triggerId) {
        queueMicrotask(() => {
          if (!cancelled) restoreFocus(triggerId);
        });
      }
    }

    previousPanelRef.current = activePanel;
    return () => {
      cancelled = true;
    };
  }, [
    activePanel,
    restoreFocusOnClose,
    returnFocusId,
    showModal,
  ]);

  useEffect(() => {
    if (previousOverlayRef.current && !isOverlay && activePanel) {
      closePanel();
    }
    previousOverlayRef.current = isOverlay;
  }, [activePanel, closePanel, isOverlay]);

  useEffect(() => {
    if (!showModal) {
      return;
    }

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented) {
        return;
      }

      if (event.key === 'Escape') {
        event.preventDefault();
        closePanel();
        return;
      }

      if (dialogRef.current) {
        trapTabKey(dialogRef.current, event);
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [closePanel, showModal]);

  const desktopInspector =
    !isOverlay && inspector ? (
      <aside className="workspace-context-rail" aria-label="论文上下文">
        {inspector}
      </aside>
    ) : null;

  if (!showModal || !activePanel) {
    return desktopInspector;
  }

  const copy = panelCopy[activePanel];
  const content =
    activePanel === 'inspector'
      ? inspector
      : activePanel === 'queue'
        ? queue
        : command;
  const presentation =
    isMobile && activePanel === 'inspector' ? 'sheet' : 'drawer';

  return (
    <>
      {desktopInspector}
      <div
        className="workspace-overlay"
        data-presentation={presentation}
        onPointerDown={(event) => {
          if (event.currentTarget === event.target) {
            closePanel();
          }
        }}
      >
        <section
          id="workspace-responsive-panel"
          ref={dialogRef}
          className="workspace-overlay__panel floating-material"
          role="dialog"
          aria-modal="true"
          aria-labelledby="workspace-panel-title"
          aria-describedby="workspace-panel-description"
          tabIndex={-1}
          data-motion={reducedMotion ? 'reduced' : 'full'}
          data-transparency={reducedTransparency ? 'reduced' : 'full'}
        >
          <header className="workspace-overlay__header">
            <div>
              <h2 id="workspace-panel-title">{copy.title}</h2>
              <p id="workspace-panel-description">{copy.description}</p>
            </div>
            <button
              type="button"
              onClick={closePanel}
              aria-label={`关闭${copy.title}`}
            >
              关闭
            </button>
          </header>
          <div className="workspace-overlay__content">{content}</div>
        </section>
      </div>
    </>
  );
}
