# React Clean-room Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and cut over to a complete React 19 research workspace while preserving the existing Node/SQLite/Python contracts and a reversible `/legacy/` entry.

**Architecture:** A standalone `frontend/` Vite application owns rendering and client state. React Router owns navigation, React Query owns server facts, Zustand owns workspace preferences, feature reducers own NDJSON sessions, and focused deep modules own PDF, Markdown, motion, and chart lifecycles. The CommonJS server exposes `/workspace/*`, `/legacy/*`, and an environment-controlled root without co-mounting the applications.

**Tech Stack:** React 19, TypeScript, Vite, React Router, Zustand, TanStack Query, native Fetch/ReadableStream, PDF.js, Marked, KaTeX, GSAP/@gsap/react/Flip, ECharts, Vitest, React Testing Library, Playwright, Node test.

---

## Plan family and execution order

The specification spans several independently testable subsystems. This master plan keeps one integration order while preserving task-local file ownership:

1. Foundation and server routing.
2. Typed data/streaming core.
3. Shell and design system.
4. Dashboard, Library, and Reviews.
5. Reader PDF and safe rich content.
6. Acquire, Jobs, Insights, and Settings.
7. End-to-end verification and reversible root cutover.

Tasks 4–6 can run in parallel after Tasks 1–3 because they own disjoint feature files and consume the same published data-layer interfaces.

## Delivery snapshot — 2026-08-06

- Implementation is isolated on `codex/react-clean-room-workspace`; the stable branch and the user's main working tree are not used for the refactor.
- Tasks 1–12 are implemented on this branch. The final automated gate is green: Node 273/273, frontend Vitest 51 files / 247 tests, TypeScript, ESLint, production build, and Playwright 28/28.
- The final architecture tightens the original file map: route handles and the preference-only workspace store live behind `frontend/src/lib/workspace/index.ts`; every route is loaded through its feature `index.ts`; shared acquisition draft behavior lives behind `frontend/src/lib/research-search/index.ts`. Dependency-boundary tests enforce those seams.
- The React application is clean-room isolated under `frontend/` and is served only below `/workspace/`. It does not import or request legacy application HTML, CSS, JavaScript, or vendor bundles. The old `public/` application remains available below `/legacy/` as a reversible fallback.
- The production-entry drill passed: default `/` redirects to `/workspace/`; React deep routes refresh and survive history navigation; `/legacy/` remains functional; with `UI_ENTRY=legacy`, `/` serves the same legacy document while `/workspace/` remains reachable. The recorded Chromium smoke observed 72 requests with no legacy asset request, console/page/request failure, or CSP violation.
- Browser QA covers 21 workflow/runtime scenarios plus 7 visual tests producing 10 approved baselines at 1440, 1100, 900, 760, and 390 CSS pixels. It includes CSP/Worker/font/MIME checks, reduced-motion/transparency, mobile navigation geometry, neutral PDF paper, and 20 repeated paper switches/zooms returning canvases and Workers to baseline.
- Docker is not installed on the delivery machine, so a live container smoke could not run. The three Docker build/Compose contract tests pass, the image performs an isolated production React build, and Compose exposes the startup-only `UI_ENTRY` rollback switch. This limitation is recorded rather than presenting an unrun container test as evidence.

## File responsibility map

- `server.js`, `lib/frontend-assets.js`: three-entry static routing, cache headers, CSP, safe fallback.
- `frontend/package.json`, `frontend/vite.config.ts`, `frontend/src/main.tsx`: independent React build and test entry.
- `frontend/src/lib/api/*`: HTTP transport, decoders, DTOs, query keys, server commands.
- `frontend/src/lib/streaming/*`: NDJSON parser, terminal contracts, reducer and run ownership.
- `frontend/src/app/*`, `frontend/src/components/*`: providers, router, shell, overlays, feedback and accessibility.
- `frontend/src/features/dashboard/*`: Paper Deck state machine, inspector and timeline.
- `frontend/src/features/library/*`: filters, table, preview and paper mutations.
- `frontend/src/features/reviews/*`: review groups and authoritative snapshot mutations.
- `frontend/src/lib/pdf/*`, `frontend/src/features/reader/*`: PDF session, page rendering, selection, translation and reader composition.
- `frontend/src/lib/markdown/*`: Worker AST DTO, React adapter, KaTeX allowlist and unique HTML sink.
- `frontend/src/features/acquire/*`, `frontend/src/features/jobs/*`: candidate streams, local PDF actions, Jobs and Schedules.
- `frontend/src/lib/charts/*`, `frontend/src/lib/motion/*`, `frontend/src/features/insights/*`: ECharts and GSAP adapters plus Insights.
- `frontend/src/features/settings/*`: safe secret-preserving settings form and LLM test.
- `frontend/e2e/*`, `test/react-entry-routing.test.js`: browser workflows and server entry regression.

### Task 1: Scaffold the isolated React package and test harness

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.app.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/src/test/setup.ts`
- Create: `frontend/src/app/App.test.tsx`
- Modify: `package.json`

- [x] **Step 1: Add a failing smoke test**

```tsx
import { render, screen } from '@testing-library/react';
import { App } from './App';

it('renders the Paper Study application landmark', () => {
  render(<App />);
  expect(screen.getByRole('application', { name: 'Paper Study 研究工作区' })).toBeInTheDocument();
});
```

- [x] **Step 2: Create the independent package manifest and install exact capabilities**

`frontend/package.json` must define `dev`, `build`, `test`, `test:run`, `typecheck`, `lint`, and `e2e` scripts and include React 19, Router, Query, Zustand, GSAP, ECharts, PDF.js, Marked and KaTeX plus the stated test stack. Run:

```powershell
npm.cmd install --prefix frontend
```

Expected: a new `frontend/package-lock.json` and no changes to the root lockfile.

- [x] **Step 3: Implement the smallest typed Vite app**

```tsx
export function App() {
  return <div role="application" aria-label="Paper Study 研究工作区" />;
}
```

Configure Vite with `base: '/workspace/'`, React plugin, Vitest `jsdom`, setup file, and `/api`, `/pdfbytes`, `/papers` development proxies.

- [x] **Step 4: Run red/green checks**

```powershell
npm.cmd run test:run --prefix frontend
npm.cmd run typecheck --prefix frontend
npm.cmd run build --prefix frontend
```

Expected: smoke test PASS, typecheck PASS, production output in `frontend/dist`.

- [x] **Step 5: Add root orchestration scripts and commit**

Add root scripts `frontend:install`, `frontend:test`, `frontend:typecheck`, `frontend:build`, and `test:all` without changing the existing `test` command. Commit:

```powershell
git add package.json frontend
git commit -m "build: scaffold react workspace"
```

### Task 2: Add safe `/workspace/`, `/legacy/`, and root routing

**Files:**
- Create: `lib/frontend-assets.js`
- Create: `test/react-entry-routing.test.js`
- Modify: `server.js`
- Modify: `package.json`

- [x] **Step 1: Write failing route-resolution tests**

```js
test('explicit React and legacy entries resolve without path escape', () => {
  assert.equal(resolveFrontendPath('/workspace/library', roots).kind, 'react-html');
  assert.equal(resolveFrontendPath('/legacy/style.css', roots).kind, 'legacy-file');
  assert.equal(resolveFrontendPath('/workspace/../server.js', roots).kind, 'forbidden');
});

test('the root switch changes only slash', () => {
  assert.equal(resolveFrontendPath('/', roots, 'react').location, '/workspace/');
  assert.equal(resolveFrontendPath('/', roots, 'legacy').kind, 'legacy-html');
});
```

- [x] **Step 2: Verify the tests fail**

```powershell
node --test test/react-entry-routing.test.js
```

Expected: FAIL because `lib/frontend-assets.js` does not exist.

- [x] **Step 3: Implement a pure resolver**

```js
function inside(root, target) {
  const relative = path.relative(root, target);
  return relative === '' || (!relative.startsWith('..') && !path.isAbsolute(relative));
}

function resolveFrontendPath(pathname, roots, entry = 'react') {
  if (pathname === '/' && entry === 'react') return { kind: 'redirect', location: '/workspace/' };
  if (pathname === '/' && entry === 'legacy') return { kind: 'legacy-html', file: roots.legacyIndex };
  if (pathname === '/workspace') return { kind: 'redirect', location: '/workspace/' };
  if (pathname.startsWith('/workspace/')) {
    const relative = decodeURIComponent(pathname.slice('/workspace/'.length));
    const target = path.resolve(roots.react, relative);
    if (!inside(roots.react, target)) return { kind: 'forbidden' };
    return path.extname(relative)
      ? { kind: 'react-file', file: target }
      : { kind: 'react-html', file: roots.reactIndex };
  }
  if (pathname === '/legacy') return { kind: 'redirect', location: '/legacy/' };
  if (pathname.startsWith('/legacy/')) {
    const relative = decodeURIComponent(pathname.slice('/legacy/'.length)) || 'index.html';
    const target = path.resolve(roots.legacy, relative);
    return inside(roots.legacy, target)
      ? { kind: relative === 'index.html' ? 'legacy-html' : 'legacy-file', file: target }
      : { kind: 'forbidden' };
  }
  return { kind: 'not-found' };
}
```

The implementation must return cache metadata: HTML `no-cache`, hashed React assets `public,max-age=31536000,immutable`, legacy assets `no-cache`.

- [x] **Step 4: Wire the server after API/PDF handlers**

Read `UI_ENTRY` once at startup, accept only `react|legacy`, default to `react` on this completed branch, and fall back to legacy with a logged warning when React dist is absent. React responses receive the design CSP; legacy responses preserve current headers.

- [x] **Step 5: Run routing and baseline tests**

```powershell
node --test test/react-entry-routing.test.js
npm.cmd test
```

Expected: route tests PASS and all 249 baseline tests remain green.

- [x] **Step 6: Commit**

```powershell
git add lib/frontend-assets.js test/react-entry-routing.test.js server.js package.json
git commit -m "feat: add isolated react and legacy entries"
```

### Task 3: Build the typed API, DTO, Query, storage, and NDJSON core

**Files:**
- Create: `frontend/src/lib/api/errors.ts`
- Create: `frontend/src/lib/api/types.ts`
- Create: `frontend/src/lib/api/decoders.ts`
- Create: `frontend/src/lib/api/client.ts`
- Create: `frontend/src/lib/api/keys.ts`
- Create: `frontend/src/lib/api/paperApi.ts`
- Create: `frontend/src/lib/api/workspaceApi.ts`
- Create: `frontend/src/lib/streaming/contracts.ts`
- Create: `frontend/src/lib/streaming/ndjson.ts`
- Create feature-owned stream reducers alongside Acquire, Jobs, Insights, and Reader commands
- Create: `frontend/src/lib/storage/safeStorage.ts`
- Create: `frontend/src/lib/api/client.test.ts`
- Create: `frontend/src/lib/streaming/ndjson.test.ts`
- Create: `frontend/src/lib/storage/safeStorage.test.ts`

- [x] **Step 1: Write transport and protocol failure tests**

Cover JSON decoder failure, valid empty text, bytes, non-2xx body, preserved AbortError, split chunks, residual line, no final newline, no stream body JSON, missing terminal, duplicate terminal and post-terminal event.

```ts
await expect(api.text(request, fetchReturning(''))).resolves.toBe('');
await expect(readNdjson(response(['{"type":"pro', 'gress"}\n{"type":"result","ok":true}']))).resolves.toMatchObject({ ok: true });
```

- [x] **Step 2: Run the focused tests and confirm failure**

```powershell
npm.cmd run test:run --prefix frontend -- src/lib/api/client.test.ts src/lib/streaming/ndjson.test.ts
```

Expected: FAIL on missing modules.

- [x] **Step 3: Implement four public transport methods**

```ts
export const api = {
  json: <T>(request: RequestInfo, decode: Decoder<T>, init?: RequestInit) => requestDecoded(request, init, decode),
  text: (request: RequestInfo, init?: RequestInit) => requestBody(request, init, response => response.text()),
  bytes: (request: RequestInfo, init?: RequestInit) => requestBody(request, init, response => response.arrayBuffer()),
  ndjson: <E, R>(request: RequestInfo, contract: StreamContract<E, R>, init?: RequestInit) => readNdjson(request, contract, init),
};
```

Use unknown-input decoders for Paper, review groups, jobs, schedules, citation graph and settings. Normalize missing paper status to `未开始` at the DTO boundary only.

- [x] **Step 4: Implement endpoint terminal contracts and feature-owned session reducers**

```ts
export const resultContract = createTerminalContract('result');
export const doneContract = createTerminalContract('done');

type FeatureStreamState = {
  runId: number | null;
  phase: 'idle' | 'running' | 'success' | 'failure' | 'stopped';
  progress: readonly string[];
};
```

Review amendment: the initial generic reducer was removed because it had no production
consumer. Acquire, Jobs, Insights, Local PDF, and Reader artifacts keep their reducers
next to the domain state they own; their run identities, completion phases, and progress
retention rules are intentionally different.

- [x] **Step 5: Implement SafeStorage and tests**

SafeStorage must catch access, read, write and quota errors. Search history keeps at most 12 entries and falls back to in-memory state.

- [x] **Step 6: Run tests, typecheck, and commit**

```powershell
npm.cmd run test:run --prefix frontend
npm.cmd run typecheck --prefix frontend
git add frontend/src/lib
git commit -m "feat: add typed workspace data core"
```

### Task 4: Build providers, router, shell, responsive overlays, and design tokens

**Files:**
- Create: `frontend/src/app/providers/AppProviders.tsx`
- Create: `frontend/src/app/router.tsx`
- Create: `frontend/src/app/stores/workspaceStore.ts`
- Create: `frontend/src/components/workspace-shell/WorkspaceShell.tsx`
- Create: `frontend/src/components/navigation/GlobalNavigation.tsx`
- Create: `frontend/src/components/command-bar/CommandBar.tsx`
- Create: `frontend/src/components/overlays/ResponsivePanelHost.tsx`
- Create: `frontend/src/components/feedback/RouteErrorBoundary.tsx`
- Create: `frontend/src/components/feedback/LiveAnnouncer.tsx`
- Create: `frontend/src/lib/accessibility/focus.ts`
- Create: `frontend/src/styles/tokens.css`
- Create: `frontend/src/styles/reset.css`
- Create: `frontend/src/styles/global.css`
- Create: `frontend/src/styles/materials.css`
- Create: `frontend/src/styles/motion.css`
- Create: `frontend/src/components/workspace-shell/WorkspaceShell.test.tsx`
- Modify: `frontend/src/app/App.tsx`
- Modify: `frontend/src/main.tsx`

- [x] **Step 1: Write shell behavior tests**

Test route navigation, `aria-current`, Skip Link, page-title focus, one mobile modal at a time, Escape, trigger focus restoration, live-region throttling, and reduced-motion behavior.

```tsx
await user.click(screen.getByRole('button', { name: '论文上下文' }));
await user.keyboard('{Escape}');
expect(screen.getByRole('button', { name: '论文上下文' })).toHaveFocus();
```

- [x] **Step 2: Implement providers and one-owner store**

The store contains `workspaceSelectionId`, per-surface filters, panel state, density and theme only. It never stores a Paper DTO. QueryClient retries only network/5xx GET failures and attempts each request at most twice total.

- [x] **Step 3: Implement lazy routes and route handles**

Each route module exports its own error boundary and handle `{ title, layout }`. Reader content comes only from `:paperId`; entering Reader may record the id as last workspace selection but never reads it back to override the URL.

- [x] **Step 4: Implement the approved visual system**

Define the locked color tokens and responsive layout at `1100px` and `760px`. Add solid-surface fallback, visible focus, 44px mobile controls and reduced-motion rules. Do not import any legacy stylesheet.

- [x] **Step 5: Run tests and commit**

```powershell
npm.cmd run test:run --prefix frontend -- src/components/workspace-shell/WorkspaceShell.test.tsx
npm.cmd run typecheck --prefix frontend
git add frontend/src/app frontend/src/components frontend/src/styles frontend/src/lib/accessibility
git commit -m "feat: build react workspace shell"
```

### Task 5: Implement Dashboard and the Paper Deck state machine

**Files:**
- Create: `frontend/src/features/dashboard/deckReducer.ts`
- Create: `frontend/src/features/dashboard/deckReducer.test.ts`
- Create: `frontend/src/features/dashboard/PaperDeck.tsx`
- Create: `frontend/src/features/dashboard/PaperInspector.tsx`
- Create: `frontend/src/features/dashboard/ResearchTimeline.tsx`
- Create: `frontend/src/features/dashboard/DashboardRoute.tsx`
- Create: `frontend/src/lib/motion/gsap.ts`
- Create: `frontend/src/lib/motion/useDeckFlip.ts`

- [x] **Step 1: Write reducer tests for every invariant**

```ts
expect(reconcile([], 'p1')).toEqual({ ids: [], selectedIndex: -1 });
expect(reconcile([p1], undefined).visible).toEqual(['p1']);
expect(move(stateAtStart, -1).selectedIndex).toBe(0);
expect(reconcile(filtered, preservedId).selectedId).toBe(preservedId);
expect(reconcile(filteredWithoutSelection, preservedId).selectedId).toBe(filteredWithoutSelection[0].id);
```

Also test at most five real cards, exact total, single click select, Enter/double-click open and no wrapping.

- [x] **Step 2: Implement the pure reducer and accessible deck**

Use `listbox/option`, roving tabindex and explicit previous/next/open actions. Reducer code must not navigate, focus or animate.

- [x] **Step 3: Add GSAP presentation only**

Register `Flip` and `useGSAP` once in `lib/motion/gsap.ts`. `useDeckFlip` receives committed deck state, uses scoped refs/contextSafe, and skips displacement under reduced motion.

- [x] **Step 4: Build inspector and truthful timeline**

Consume Papers, Reviews and Jobs queries. Derive only evidence-backed events; use explanatory empty state when no event exists.

- [x] **Step 5: Verify and commit**

```powershell
npm.cmd run test:run --prefix frontend -- src/features/dashboard
npm.cmd run typecheck --prefix frontend
git add frontend/src/features/dashboard frontend/src/lib/motion
git commit -m "feat: add research dashboard and paper deck"
```

### Task 6: Implement Library mutations and Reviews snapshots

**Files:**
- Create: `frontend/src/features/library/filters.ts`
- Create: `frontend/src/features/library/LibraryRoute.tsx`
- Create: `frontend/src/features/library/PaperTable.tsx`
- Create: `frontend/src/features/library/PaperPreview.tsx`
- Create: `frontend/src/features/library/PaperEditor.tsx`
- Create: `frontend/src/features/library/LibraryRoute.test.tsx`
- Create: `frontend/src/features/reviews/ReviewsRoute.tsx`
- Create: `frontend/src/features/reviews/ReviewGroup.tsx`
- Create: `frontend/src/features/reviews/ReviewsRoute.test.tsx`

- [x] **Step 1: Write filter and mutation tests**

Cover bilingual search, venue/type/topic, status/favorite/year/source combinations, five sort modes, semantic score order, optimistic favorite/status rollback, add/update/delete failure, and selection preservation.

- [x] **Step 2: Implement Library read behavior**

Build a dense semantic table with batch selection and fixed preview. Batch selection remains client-only. Empty results distinguish ordinary, favorite and semantic cases.

- [x] **Step 3: Implement paper mutations with fixed ids**

Every mutation closes over the paper id passed to the handler. On success patch exact keys; on failure restore the previous cache. Never read a later `workspaceSelectionId` after await.

- [x] **Step 4: Write and implement Reviews snapshot tests**

```tsx
server.use(completeReviewReturning({ ok: true, plan, reviews: authoritativeGroups }));
await user.click(screen.getByRole('button', { name: '完成本轮' }));
expect(queryClient.getQueryData(reviewKeys.list())).toEqual(authoritativeGroups);
```

Reject stale pre-mutation loads and use the returned reviews object as one atomic cache value.

- [x] **Step 5: Run tests and commit**

```powershell
npm.cmd run test:run --prefix frontend -- src/features/library src/features/reviews
npm.cmd run typecheck --prefix frontend
git add frontend/src/features/library frontend/src/features/reviews
git commit -m "feat: add library and review workflows"
```

### Task 7: Implement the PDF session, lazy pages, selection, and translation

**Files:**
- Create: `frontend/src/lib/pdf/PdfReaderSession.ts`
- Create: `frontend/src/lib/pdf/PageViewportAnchor.ts`
- Create: `frontend/src/lib/pdf/selectionPolicy.ts`
- Create: `frontend/src/lib/pdf/PdfSelectionController.ts`
- Create: `frontend/src/lib/pdf/SelectionTranslator.ts`
- Create: `frontend/src/lib/pdf/PdfReaderSession.test.ts`
- Create: `frontend/src/lib/pdf/selectionPolicy.test.ts`
- Create: `frontend/src/features/reader/PdfWorkspace.tsx`
- Create: `frontend/src/features/reader/PdfPage.tsx`

- [x] **Step 1: Write lifecycle tests before implementation**

Test unresolved loading task switch, resolved document switch, zoom page-only rebuild, idempotent dispose, fetch abort, page/text cancel, observer disconnect, canvas reset and final live-resource count zero.

```ts
session.open('a');
session.open('b');
expect(firstLoadingTask.destroy).toHaveBeenCalledOnce();
expect(firstDocumentDestroy).not.toHaveBeenCalled();
```

- [x] **Step 2: Implement the generation-based session**

`open` increments generation and creates one owner. `setZoom` preserves document, cancels page resources and restores a relative page anchor. `dispose` chooses loading-task or document destruction based on state and is idempotent.

- [x] **Step 3: Write and implement selection-policy tests**

Cover left/right column lock, gutter detection, 0.7 median font threshold, native fallback, hyphen merge, hard-line merge, paragraph separators and 6000-character rejection without truncation.

- [x] **Step 4: Implement the selection controller and translator**

Controller owns every listener/timer/popover/native selection. Zoom clears transient selection but preserves text fragments; paper switch clears all. Translator aborts on new request or paper generation and checks request id, paper id and generation before commit.

- [x] **Step 5: Build lazy PDF pages**

Use one IntersectionObserver for page activation and one ResizeObserver for the viewport. Canvas and text layer share the same viewport transform. Configure the PDF worker through Vite and `isEvalSupported:false`.

- [x] **Step 6: Run stress-focused tests and commit**

```powershell
npm.cmd run test:run --prefix frontend -- src/lib/pdf src/features/reader
npm.cmd run typecheck --prefix frontend
git add frontend/src/lib/pdf frontend/src/features/reader
git commit -m "feat: add lifecycle-safe pdf reader"
```

### Task 8: Implement safe Markdown/KaTeX and complete Reader artifacts

**Files:**
- Create: `frontend/src/lib/markdown/ast.ts`
- Create: `frontend/src/lib/markdown/markdown.worker.ts`
- Create: `frontend/src/lib/markdown/workerClient.ts`
- Create: `frontend/src/lib/markdown/MarkdownContent.tsx`
- Create: `frontend/src/lib/markdown/TrustedMathHtml.tsx`
- Create: `frontend/src/lib/markdown/katexAllowlist.ts`
- Create: `frontend/src/lib/markdown/markdown.test.tsx`
- Create: `frontend/src/features/reader/ArtifactPanel.tsx`
- Create: `frontend/src/features/reader/ReaderRoute.tsx`
- Create: `frontend/src/features/reader/ReaderRoute.test.tsx`

- [x] **Step 1: Write hostile-input and Worker race tests**

Cover raw HTML, images, javascript/data/file/relative URLs, safe absolute URLs, malformed math, KaTeX trust options, pathological emphasis, timeout fallback, late message after terminate and new generation superseding old work.

- [x] **Step 2: Define a structured-clone AST DTO**

```ts
export type SafeNode =
  | { type: 'text'; value: string }
  | { type: 'paragraph'; children: SafeNode[] }
  | { type: 'link'; href: string; children: SafeNode[] }
  | { type: 'code'; value: string; language?: string }
  | { type: 'math'; value: string; display: boolean };
```

Worker returns `{ version: 1, nodes: SafeNode[] }`; it never returns React nodes or rendered HTML.

- [x] **Step 3: Implement the Worker client and React adapter**

Use `new Worker(new URL('./markdown.worker.ts', import.meta.url), { type: 'module' })`. Timer, message/error/messageerror listeners and Worker are all owned by one request. Failure returns a plain text node.

- [x] **Step 4: Implement the unique math sink**

KaTeX uses `trust:false`, `maxExpand:1000`. Sanitize allowed KaTeX/MathML tags and attributes before `TrustedMathHtml`; only that component contains `dangerouslySetInnerHTML`.

- [x] **Step 5: Complete Reader artifacts with fixed paper identity**

Read note/explainer/translation via `api.text`, treating `''` as an empty state. Save/generate commands capture route paper id, abort on route change and invalidate only the matching artifact key.

- [x] **Step 6: Run tests and commit**

```powershell
npm.cmd run test:run --prefix frontend -- src/lib/markdown src/features/reader
npm.cmd run typecheck --prefix frontend
git add frontend/src/lib/markdown frontend/src/features/reader
git commit -m "feat: add safe research artifacts"
```

### Task 9: Implement Acquire streams, Jobs, and Schedules

**Files:**
- Create: `frontend/src/features/acquire/acquireReducer.ts`
- Create: `frontend/src/features/acquire/AcquireRoute.tsx`
- Create: `frontend/src/features/acquire/CandidateList.tsx`
- Create: `frontend/src/features/acquire/LocalPdfPanel.tsx`
- Create: `frontend/src/features/acquire/AcquireRoute.test.tsx`
- Create: `frontend/src/features/jobs/JobsRoute.tsx`
- Create: `frontend/src/features/jobs/JobDetail.tsx`
- Create: `frontend/src/features/jobs/SchedulesPanel.tsx`
- Create: `frontend/src/features/jobs/JobsRoute.test.tsx`

- [x] **Step 1: Write stream and side-effect reconciliation tests**

Test source/query validation, max clamp, progress/candidates, explicit retry, stop-receiving copy, ingest partial success, failed Job confirm with changed candidate state, added-paper invalidation and route-scoped polling.

- [x] **Step 2: Implement Acquire session ownership**

One reducer owns one run. Starting a run aborts the prior owner. Search/verify results stay in session; ingest completion, failure or cancel marks papers stale according to the endpoint rule. Search history writes through SafeStorage.

- [x] **Step 3: Implement local PDF actions**

Expose scan/import/download progress with TOTAL/PARSED/ADDED/DUP/SKIP and failure details. Do not hide partial success.

- [x] **Step 4: Implement Jobs and Schedules**

Show pending/running/review/done/failed and true zero state. Poll only active jobs at 2–3 seconds. Do not add an ignore action. Any confirm terminal refreshes job detail/list; `added>0` refreshes papers. Implement schedule create/toggle/delete with visible server-confirmed status.

- [x] **Step 5: Run tests and commit**

```powershell
npm.cmd run test:run --prefix frontend -- src/features/acquire src/features/jobs
npm.cmd run typecheck --prefix frontend
git add frontend/src/features/acquire frontend/src/features/jobs
git commit -m "feat: add acquisition and job workflows"
```

### Task 10: Implement Insights charts and Settings

**Files:**
- Create: `frontend/src/lib/charts/useEChart.ts`
- Create: `frontend/src/lib/charts/options.ts`
- Create: `frontend/src/lib/charts/useEChart.test.tsx`
- Create: `frontend/src/features/insights/InsightsRoute.tsx`
- Create: `frontend/src/features/insights/InsightsRoute.test.tsx`
- Create: `frontend/src/features/settings/SettingsRoute.tsx`
- Create: `frontend/src/features/settings/SettingsRoute.test.tsx`

- [x] **Step 1: Write chart lifecycle tests**

Assert no init for zero-size/empty data, one live instance after StrictMode remount, rAF resize coalescing, escaped tooltip content and cleanup order `cancel → disconnect → off → dispose → clear`.

- [x] **Step 2: Implement chart adapter and Insights**

Derive trend/tree from papers; request citation graph from the server. Preserve `src cites dst`. Re-fetch after build, open paper nodes, and use explanatory empty states.

- [x] **Step 3: Write Settings contract tests**

Verify masked tails, blank secret inputs, preserve-on-empty payloads, every directory/research/embedding field, `{ok,output}` LLM test handling, and independent save/test failure states.

- [x] **Step 4: Implement Settings**

Keep form draft local. Never put Settings DTO in Zustand. Submit only typed server fields; never submit appearance preferences or mask strings.

- [x] **Step 5: Run tests and commit**

```powershell
npm.cmd run test:run --prefix frontend -- src/lib/charts src/features/insights src/features/settings
npm.cmd run typecheck --prefix frontend
git add frontend/src/lib/charts frontend/src/features/insights frontend/src/features/settings
git commit -m "feat: add insights and settings workspace"
```

### Task 11: Add Playwright workflows, clean-room assertions, and visual baselines

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/fixtures/mockApi.ts`
- Create: `frontend/e2e/workspace.spec.ts`
- Create: `frontend/e2e/reader.spec.ts`
- Create: `frontend/e2e/acquire-jobs.spec.ts`
- Create: `frontend/e2e/accessibility.spec.ts`
- Create: `frontend/e2e/clean-room.spec.ts`
- Create: `frontend/e2e/visual.spec.ts`

- [x] **Step 1: Build deterministic API fixtures**

Mock the full response families with real DTO shapes, including zero jobs, one/many papers, review groups, a small PDF fixture, progress/result and progress/done streams, errors and partial success. Tests must not mutate the user's live database.

- [x] **Step 2: Add user-workflow tests**

Cover every route, deck keyboard/open behavior, Library filters/mutations, Reviews, Reader PDF/selection/artifacts, Acquire, Jobs/Schedules, Insights and Settings.

- [x] **Step 3: Add accessibility and responsive tests**

Run at 1440×900, 900×900 and 390×844. Verify Skip Link, visible focus, Escape, focus restoration, bottom navigation/sheet coexistence, 44px targets and reduced motion.

- [x] **Step 4: Add clean-room and console assertions**

Record network requests and fail on any legacy application HTML/CSS/JS. Fail on console error/warn, CSP violation, missing Worker/font/MIME, or deep-route refresh failure.

- [x] **Step 5: Add visual assertions**

Capture the five approved views. Use bounded screenshot assertions for layout stability and manual comparison against the approved HTML/PNG artifacts; never compare to old UI screenshots.

- [x] **Step 6: Run and commit**

```powershell
npm.cmd run build --prefix frontend
npm.cmd run e2e --prefix frontend
git add frontend/e2e frontend/playwright.config.ts
git commit -m "test: cover react workspace workflows"
```

### Task 12: Verify production, exercise rollback, and finalize the branch

**Files:**
- Modify: `README.md`
- Modify: `docs/superpowers/plans/2026-08-05-react-clean-room-workspace.md`

- [x] **Step 1: Run every automated gate from a clean production build**

```powershell
npm.cmd test
npm.cmd run frontend:test
npm.cmd run frontend:typecheck
npm.cmd run frontend:build
npm.cmd run test:all
npm.cmd run e2e --prefix frontend
```

Expected: all commands exit 0.

- [x] **Step 2: Start the production server and smoke-test both entries**

With default React entry, verify `/` redirects to `/workspace/`, `/workspace/dashboard` renders, `/workspace/reader/:id` deep refreshes, and `/legacy/` remains functional.

- [x] **Step 3: Exercise rollback**

Restart with `UI_ENTRY=legacy`; verify `/` renders the old app and `/workspace/` remains reachable. Restore React default after the recorded drill.

- [x] **Step 4: Run leak and browser QA**

Repeat route changes, paper switches and zoom changes 20 times. Confirm no rising live resource count, no console error/warn, neutral PDF paper, correct 1440×900 and 390×844 layouts, reduced-motion and no-backdrop fallbacks.

- [x] **Step 5: Document launch, entries, rollback, and the non-deletion gate**

README must state exact install/build/start commands, both explicit URLs, `UI_ENTRY`, restart requirement and that legacy deletion is deferred until two releases or 14 recorded active-use days plus a new review.

- [x] **Step 6: Mark plan checkboxes, inspect the final diff, and commit**

```powershell
git diff --check
git status --short
git add README.md docs/superpowers/plans/2026-08-05-react-clean-room-workspace.md
git commit -m "docs: finalize react workspace delivery"
```
