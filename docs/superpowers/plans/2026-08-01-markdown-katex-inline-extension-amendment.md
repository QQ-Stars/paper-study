# Markdown / KaTeX inline-extension Amendment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace pre-parse formula protection with a per-render Marked inline math extension that preserves Markdown token boundaries and isolates unsafe contexts.

**Architecture:** Marked's inline lexer emits private `math` tokens for the four supported delimiter pairs. A renderer callback turns those tokens into KaTeX output, while a token walk downgrades math beneath raw HTML and image-alt boundaries to escaped literal text. URL, HTML, code, and image policies remain in the existing safe renderer. The KaTeX getter is read once per render and conversion failures are contained.

**Tech Stack:** Browser/CommonJS JavaScript, Marked v15, KaTeX v0.17, Node `node:test`; no new dependencies.

---

## Task 1: Replace global formula protection with a scoped Marked extension

**Files:**

- Modify: `public/markdown-rendering.js`
- Test: `test/markdown-rendering.test.js`

- [ ] **Step 1: Confirm the RED baseline**

Run `node --test test/markdown-rendering.test.js` and record the four known failures: unsafe-context pairing, currency boundary, and both image-alt isolation cases.

- [ ] **Step 2: Add the per-render inline extension**

Implement `createMathExtension()` with `start(src)`/`startInline(src)`, a tokenizer that returns `{ type: 'math', raw, text, display }`, and a `renderer.math` callback. Use Marked's inline lexer so code/link/image/HTML boundaries are already established. Match `$$…$$` and `\\[…\\]` as display math, `\\(…\\)` and `$…$` as inline math; reject escaped openers, empty bodies, newline-spanning single-dollar bodies, and currency-like boundaries.

- [ ] **Step 3: Isolate unsafe token subtrees**

Walk the parsed token tree after lexing. For `html` tokens and `image` alt tokens, replace descendant `math` tokens with their literal `raw` text using a fresh context for each image alt. Preserve math in ordinary text and link labels. Keep raw HTML, image, and unsafe-link output escaped/plain text.

- [ ] **Step 4: Harden conversion boundaries**

Convert the input with a `text()` helper that catches exceptions from `String(value)`. Read `getKatex()` once per render, then render every allowed math token with the fixed safe options. If the getter or `renderToString` throws, return escaped formula source without throwing.

- [ ] **Step 5: Run the focused suite**

Run `node --test test/markdown-rendering.test.js`. Expected result: every module-level test passes, including all context-isolation and currency cases.

- [ ] **Step 6: Inspect the diff**

Run `git diff -- public/markdown-rendering.js test/markdown-rendering.test.js` and `git diff --check`. Do not stage `package-lock.json`.

## Task 2: Wire the renderer into the page

**Files:**

- Modify: `public/index.html`
- Modify: `public/app.js`
- Test: `test/markdown-rendering.test.js`

- [ ] **Step 1: Add the page-wiring regression test**

Assert that `markdown-rendering.js` loads before `app.js`, that `app.js` creates the renderer with lazy `window.marked`/`window.katex` getters, and that `renderMd` delegates without direct `marked.parse`, `katex.renderToString`, or `innerHTML` logic.

- [ ] **Step 2: Verify the new assertion is RED before wiring**

Run `node --test test/markdown-rendering.test.js`; the new wiring assertion must fail against the current page.

- [ ] **Step 3: Make the minimal wiring edits**

Load `markdown-rendering.js` immediately before `ingest-rendering.js`/`app.js`. Replace the old `renderMd` implementation with one renderer instance and `renderInto` delegation; leave all call sites and DOM IDs unchanged.

- [ ] **Step 4: Run focused and JavaScript suites**

Run `node --test test/markdown-rendering.test.js` and `npm.cmd test`; both must exit zero.

## Task 3: Repository and browser verification

- [ ] Run `.venv\\Scripts\\python.exe -m unittest discover -s test -p "test_*.py"`.
- [ ] Run a local browser smoke test with safe Markdown, raw HTML, a tracking image, a dangerous link, and a formula; verify only the safe link/formula become active DOM.
- [ ] Run `git diff --check` and inspect `git status --short`; preserve unrelated user changes and never stage `package-lock.json` unless it has a real intentional diff.

