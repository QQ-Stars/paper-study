import { QueryClient } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { App } from '../../app/App';
import {
  createWorkspaceQueryClient,
  shouldRetryWorkspaceQuery,
} from '../../app/providers/queryClient';
import { createWorkspaceMemoryRouter } from '../../app/router';
import {
  resetWorkspaceStore,
  useWorkspaceStore,
} from '../../lib/workspace';
import { NetworkError } from '../../lib/api/errors';
import {
  LiveAnnouncer,
} from '../feedback/LiveAnnouncer';
import { announceWorkspace } from '../feedback/announcements';
import { ResponsivePanelHost } from '../overlays/ResponsivePanelHost';
import { PaperInspector } from '../../features/dashboard/PaperInspector';

type MediaOverrides = {
  mobile?: boolean;
  overlay?: boolean;
  reducedMotion?: boolean;
  reducedTransparency?: boolean;
};

function installMatchMedia({
  mobile = false,
  overlay = false,
  reducedMotion = false,
  reducedTransparency = false,
}: MediaOverrides = {}) {
  const listeners = new Map<
    string,
    Set<
      | EventListenerOrEventListenerObject
      | NonNullable<MediaQueryList['onchange']>
    >
  >();

  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn((query: string): MediaQueryList => {
      const matches =
        (query.includes('max-width: 760px') && mobile) ||
        (query.includes('max-width: 1099px') && overlay) ||
        (query.includes('prefers-reduced-motion') && reducedMotion) ||
        (query.includes('prefers-reduced-transparency') &&
          reducedTransparency);
      const queryListeners = listeners.get(query) ?? new Set();
      listeners.set(query, queryListeners);
      const addEventListener = ((
        _type: string,
        listener: EventListenerOrEventListenerObject,
      ) => queryListeners.add(listener)) as MediaQueryList['addEventListener'];
      const removeEventListener = ((
        _type: string,
        listener: EventListenerOrEventListenerObject,
      ) => queryListeners.delete(listener)) as MediaQueryList['removeEventListener'];

      const mediaQueryList: MediaQueryList = {
        matches,
        media: query,
        onchange: null,
        addEventListener,
        removeEventListener,
        addListener: (listener) => {
          if (listener) {
            queryListeners.add(listener);
          }
        },
        removeListener: (listener) => {
          if (listener) {
            queryListeners.delete(listener);
          }
        },
        dispatchEvent: () => true,
      };
      return mediaQueryList;
    }),
  });
}

function renderWorkspace(initialEntry = '/reviews') {
  const router = createWorkspaceMemoryRouter([initialEntry]);
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  const view = render(<App router={router} queryClient={queryClient} />);
  return { ...view, queryClient, router };
}

beforeEach(() => {
  installMatchMedia();
  resetWorkspaceStore();
  document.title = '';
});

afterEach(() => {
  vi.useRealTimers();
});

it('navigates with aria-current and focuses the destination title', async () => {
  const user = userEvent.setup();
  renderWorkspace();

  const reviewsLink = await screen.findByRole('link', { name: '复习' });
  expect(reviewsLink).toHaveAttribute('aria-current', 'page');

  await user.click(screen.getByRole('link', { name: '文献库' }));

  const title = await screen.findByRole('heading', {
    level: 1,
    name: '文献库',
  });
  expect(within(screen.getByLabelText('工作区命令')).getByRole('heading', {
    level: 1,
    name: '文献库',
  })).toBe(title);
  expect(document.querySelector('.workspace-page-header')).not.toBeInTheDocument();
  await waitFor(() => expect(title).toHaveFocus());
  expect(screen.getByRole('link', { name: '文献库' })).toHaveAttribute(
    'aria-current',
    'page',
  );
  expect(reviewsLink).not.toHaveAttribute('aria-current');
  expect(document.title).toBe('文献库 | Paper Study');
});

it('focuses the Reader-owned state title when navigation starts loading it', async () => {
  const { router } = renderWorkspace('/reviews');
  await screen.findByRole('heading', { level: 1, name: '复习' });

  await act(async () => {
    await router.navigate('/reader/focus-target');
  });

  await waitFor(() => {
    const title = document.getElementById('workspace-page-title');
    expect(title).toHaveAttribute('tabindex', '-1');
    expect(title).toHaveFocus();
  });
});

it('moves keyboard users from the Skip Link to the main workspace', async () => {
  const user = userEvent.setup();
  renderWorkspace('/reviews');

  const skipLink = await screen.findByRole('link', { name: '跳到主要内容' });
  await user.click(skipLink);

  expect(screen.getByRole('main')).toHaveFocus();
});

it('keeps at most one mobile modal open and restores the latest trigger on Escape', async () => {
  installMatchMedia({ mobile: true, overlay: true });
  const user = userEvent.setup();
  renderWorkspace();

  const contextTrigger = await screen.findByRole('button', {
    name: '论文上下文',
  });
  await user.click(contextTrigger);
  expect(
    screen.getByRole('dialog', { name: '论文上下文' }),
  ).toBeInTheDocument();
  expect(document.body).toHaveStyle({ overflow: 'hidden' });

  const queueTrigger = screen.getByRole('button', { name: '研究队列' });
  await user.click(queueTrigger);

  expect(screen.getAllByRole('dialog')).toHaveLength(1);
  expect(
    screen.getByRole('dialog', { name: '研究队列' }),
  ).toBeInTheDocument();

  await user.keyboard('{Escape}');

  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(queueTrigger).toHaveFocus();
  expect(document.body.style.overflow).toBe('');
});

it('lets the mobile sheet own the embedded inspector title and surface', () => {
  installMatchMedia({ mobile: true, overlay: true });
  useWorkspaceStore.getState().openPanel('inspector', 'dashboard-inspector-trigger');

  render(
    <ResponsivePanelHost
      inspector={(
        <PaperInspector
          paper={{ id: 'sheet-paper', title: 'Sheet Paper', status: '学习中' }}
          mode="rail"
          open
          embedded
          onClose={vi.fn()}
          onOpenPaper={vi.fn()}
        />
      )}
    />,
  );

  const dialog = screen.getByRole('dialog', { name: '论文上下文' });
  expect(
    within(dialog).getAllByRole('heading', { name: '论文上下文' }),
  ).toHaveLength(1);
  expect(
    within(dialog).queryByRole('region', { name: '论文上下文' }),
  ).not.toBeInTheDocument();
  expect(dialog).toHaveTextContent('Sheet Paper');
});

it('keeps the embedded inspector title and surface in the desktop rail', () => {
  render(
    <ResponsivePanelHost
      inspector={(
        <PaperInspector
          paper={{ id: 'rail-paper', title: 'Rail Paper', status: '学习中' }}
          mode="rail"
          open
          embedded
          onClose={vi.fn()}
          onOpenPaper={vi.fn()}
        />
      )}
    />,
  );

  const rail = screen.getByRole('complementary', { name: '论文上下文' });
  expect(
    within(rail).getByRole('region', { name: '论文上下文' }),
  ).toBeInTheDocument();
  expect(
    within(rail).getByRole('heading', { name: '论文上下文' }),
  ).toBeInTheDocument();
  expect(rail).toHaveTextContent('Rail Paper');
});

it('keeps the command dialog available on desktop and restores its trigger', async () => {
  const user = userEvent.setup();
  const { router } = renderWorkspace('/library');

  await act(async () => {
    await router.navigate('/insights');
  });
  expect(
    await screen.findByRole('heading', { level: 1, name: '洞察' }),
  ).toBeInTheDocument();
  await act(async () => {
    await router.navigate('/library');
  });

  const commandTrigger = await screen.findByRole('button', {
    name: /搜索或运行命令/,
  });
  await user.click(commandTrigger);

  expect(screen.getByRole('dialog', { name: '命令栏' })).toBeInTheDocument();
  await waitFor(() =>
    expect(
      screen.getByRole('searchbox', { name: '筛选工作区命令' }),
    ).toHaveFocus(),
  );
  await user.keyboard('{Escape}');

  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  expect(commandTrigger).toHaveFocus();

  await user.click(commandTrigger);
  await user.type(
    screen.getByRole('searchbox', { name: '筛选工作区命令' }),
    '洞察',
  );
  await user.click(screen.getByRole('button', { name: '查看研究洞察' }));

  const title = await screen.findByRole('heading', {
    level: 1,
    name: '洞察',
  });
  await waitFor(() => expect(title).toHaveFocus());
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('coalesces stage announcements while terminal announcements remain immediate', () => {
  vi.useFakeTimers();
  render(<LiveAnnouncer throttleMs={80} />);

  const region = screen.getByRole('status');
  act(() => {
    announceWorkspace({ kind: 'stage', message: '正在读取论文' });
    announceWorkspace({ kind: 'stage', message: '正在整理结果' });
  });
  expect(region).toBeEmptyDOMElement();

  act(() => vi.advanceTimersByTime(79));
  expect(region).toBeEmptyDOMElement();

  act(() => vi.advanceTimersByTime(1));
  expect(region).toHaveTextContent('正在整理结果');

  act(() => {
    announceWorkspace({ kind: 'complete', message: '整理完成' });
  });
  expect(region).toHaveTextContent('整理完成');
});

it('publishes reduced-motion presentation state without changing panel behavior', async () => {
  installMatchMedia({
    mobile: true,
    overlay: true,
    reducedMotion: true,
    reducedTransparency: true,
  });
  const user = userEvent.setup();
  renderWorkspace();

  await user.click(
    await screen.findByRole('button', { name: '论文上下文' }),
  );

  const dialog = screen.getByRole('dialog', { name: '论文上下文' });
  expect(dialog).toHaveAttribute('data-motion', 'reduced');
  expect(dialog).toHaveAttribute('data-transparency', 'reduced');

  await user.keyboard('{Escape}');
  await waitFor(() => expect(dialog).not.toBeInTheDocument());
});

it('keeps Reader URL-owned while recording the latest paper selection one way', async () => {
  const { router } = renderWorkspace('/reader/paper-alpha');

  expect(await screen.findByText('paper-alpha')).toBeInTheDocument();
  await waitFor(() =>
    expect(useWorkspaceStore.getState().workspaceSelectionId).toBe(
      'paper-alpha',
    ),
  );

  const contextTrigger = screen.getByRole('button', { name: '论文上下文' });
  contextTrigger.focus();
  await act(async () => {
    await router.navigate('/reader/paper-beta');
  });

  expect(await screen.findByText('paper-beta')).toBeInTheDocument();
  expect(useWorkspaceStore.getState().workspaceSelectionId).toBe('paper-beta');
  expect(contextTrigger).toHaveFocus();
});

it('limits Query retries to one retry for GET network and 5xx failures', () => {
  const networkError = new NetworkError(new TypeError('fetch failed'));
  expect(shouldRetryWorkspaceQuery(0, networkError)).toBe(true);
  expect(shouldRetryWorkspaceQuery(1, networkError)).toBe(false);
  expect(shouldRetryWorkspaceQuery(0, new TypeError('decoder failed'))).toBe(
    false,
  );
  expect(shouldRetryWorkspaceQuery(0, { status: 503 })).toBe(true);
  expect(shouldRetryWorkspaceQuery(0, { status: 404 })).toBe(false);
  expect(
    shouldRetryWorkspaceQuery(0, {
      name: 'AbortError',
      status: 503,
    }),
  ).toBe(false);
  expect(
    shouldRetryWorkspaceQuery(0, {
      requestMethod: 'POST',
      status: 503,
    }),
  ).toBe(false);
  expect(
    shouldRetryWorkspaceQuery(
      0,
      new NetworkError(new TypeError('post failed'), 'POST'),
    ),
  ).toBe(false);

  const client = createWorkspaceQueryClient();
  expect(client.getDefaultOptions().mutations?.retry).toBe(false);
  expect(client.getDefaultOptions().queries?.retry).toBe(
    shouldRetryWorkspaceQuery,
  );
});

it('keeps only workspace preferences and identifiers in the shell store', () => {
  const state = useWorkspaceStore.getState();

  expect(state.workspaceSelectionId).toBeNull();
  expect(state).toHaveProperty('filters.dashboard');
  expect(state).toHaveProperty('filters.library');
  expect(state).toHaveProperty('panel.active');
  expect(state).toHaveProperty('panel.restoreFocus', false);
  expect(state).toHaveProperty('density', 'compact');
  expect(state).toHaveProperty('theme', 'dark');
  expect(state).not.toHaveProperty('papers');
  expect(state).not.toHaveProperty('paper');
});

it('releases modal listeners and body locks after a StrictMode probe', () => {
  const addSpy = vi.spyOn(document, 'addEventListener');
  const removeSpy = vi.spyOn(document, 'removeEventListener');
  useWorkspaceStore.getState().openPanel('command', 'missing-trigger');

  const view = render(
    <StrictMode>
      <ResponsivePanelHost
        command={<button type="button">命令内容</button>}
      />
    </StrictMode>,
  );

  expect(screen.getByRole('dialog', { name: '命令栏' })).toBeInTheDocument();
  expect(document.body.style.overflow).toBe('hidden');

  view.unmount();

  expect(document.body.style.overflow).toBe('');
  const keydownListeners = addSpy.mock.calls
    .filter(([type]) => type === 'keydown')
    .map(([, listener]) => listener);
  expect(keydownListeners.length).toBeGreaterThan(0);
  for (const listener of keydownListeners) {
    expect(removeSpy.mock.calls).toContainEqual(['keydown', listener]);
  }

  addSpy.mockRestore();
  removeSpy.mockRestore();
});
