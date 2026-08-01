
# Markdown / KaTeX 安全渲染 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make every existing Markdown / KaTeX view render untrusted content safely without sacrificing standard Markdown, safe links, or mathematics.

**Architecture:** Add a small browser/CommonJS-compatible renderer module that owns Markdown parsing, raw-HTML/image suppression, URL validation, formula placeholder restoration, and KaTeX safe options. app.js becomes a thin delegate, so all six existing rich-text call sites retain their behavior while sharing one policy. The module resolves marked and KaTeX through getters at render time, which is compatible with the deferred KaTeX scripts in the page.

**Tech Stack:** Browser JavaScript, marked v15.0.12, KaTeX v0.17.0, Node node:test, existing static HTML assets.

---

## File structure

- Create: public/markdown-rendering.js — isolated security policy and rendering API.
- Modify: public/index.html — load the renderer before app.js.
- Modify: public/app.js — remove the direct marked / KaTeX implementation and delegate to the module.
- Create: test/markdown-rendering.test.js — runtime security, compatibility, KaTeX-option, and page-wiring regression tests.

## Task 1: Establish failing security and compatibility tests

**Files:**

- Create: test/markdown-rendering.test.js

- [ ] **Step 1: Write the failing module-level tests**

Create the test file with this complete content. It uses the exact local parser/runtime that the browser uses; no new test dependency is required.

    const assert = require('node:assert/strict');
    const fs = require('node:fs');
    const path = require('node:path');
    const test = require('node:test');

    const markedApi = require('../public/vendor/marked.min.js');
    const marked = markedApi.marked;
    const katex = require('katex');
    const { createMarkdownRenderer } = require('../public/markdown-rendering');

    function makeRenderer(katexImpl = katex) {
      return createMarkdownRenderer({
        getMarked: () => marked,
        getKatex: () => katexImpl,
      });
    }

    test('renders standard Markdown, safe links, and inline mathematics', () => {
      const html = makeRenderer().render('# Topic\n\n**bold**\n\n- one\n\n[paper](https://example.com/paper)\n\n$x^2$');

      assert.match(html, /<h1>Topic<\/h1>/);
      assert.match(html, /<strong>bold<\/strong>/);
      assert.match(html, /<li>one<\/li>/);
      assert.match(html, /<a href="https:\/\/example\.com\/paper">paper<\/a>/);
      assert.match(html, /class="katex"/);
    });

    test('renders raw HTML only as literal text', () => {
      const html = makeRenderer().render('<img src=x onerror=alert(1)> <svg onload=alert(2)></svg> <script>alert(3)</script> <p onclick=alert(4)>x</p>');

      assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
      assert.doesNotMatch(html, /<(?:img|svg|script)\b/i);
      assert.doesNotMatch(html, /<p\b[^>]*\bonclick=/i);
    });

    test('keeps only http, https, and mailto links', () => {
      const renderer = makeRenderer();
      const safe = renderer.render('[web](HTTPS://example.com/a) [mail](mailto:user@example.com)');
      assert.match(safe, /<a href="https:\/\/example\.com\/a">web<\/a>/);
      assert.match(safe, /<a href="mailto:user@example\.com">mail<\/a>/);

      for (const href of [
        'JaVaScRiPt:alert(1)',
        'data:text/html;base64,PHNjcmlwdD4=',
        'blob:https://example.com/id',
        'file:///C:/private.txt',
        '//tracker.example/pixel',
      ]) {
        const html = renderer.render('[blocked](' + href + ')');
        assert.match(html, /blocked/);
        assert.doesNotMatch(html, /<a\b/i);
      }
    });

    test('turns Markdown images into alt text without a requestable element', () => {
      const html = makeRenderer().render('![diagram](https://tracker.example/pixel.png "tracked")');

      assert.match(html, /diagram/);
      assert.doesNotMatch(html, /<img\b/i);
      assert.doesNotMatch(html, /tracker\.example/);
    });

    test('pins KaTeX to its safe option boundary', () => {
      const calls = [];
      const fakeKatex = {
        renderToString(tex, options) {
          calls.push({ tex, options });
          return '<span class="katex">safe formula</span>';
        },
      };

      const html = makeRenderer(fakeKatex).render('$$x^2$$');

      assert.match(html, /safe formula/);
      assert.deepEqual(calls, [{
        tex: 'x^2',
        options: { displayMode: true, throwOnError: false, trust: false, maxExpand: 1000 },
      }]);
    });

    test('does not turn hostile or malformed mathematics into active HTML', () => {
      const renderer = makeRenderer();
      const hostile = renderer.render('$\\href{javascript:alert(1)}{click}$ $\\htmlStyle{color:red}{x}$');

      assert.doesNotMatch(hostile, /<a\b/i);
      assert.doesNotMatch(hostile, /href\s*=\s*["']\s*javascript:/i);
      assert.doesNotThrow(() => renderer.render('$\\frac{1$'));
      assert.doesNotThrow(() => renderer.render('$$\\def\\a{\\a}\\a$$'));
    });

    test('falls back to escaped source when KaTeX is unavailable or fails', () => {
      const unavailable = makeRenderer(null).render('$<img src=x onerror=alert(1)>$');
      const broken = makeRenderer({ renderToString() { throw new Error('broken'); } }).render('$<svg onload=alert(1)>$');

      assert.doesNotMatch(unavailable, /<img\b/i);
      assert.doesNotMatch(broken, /<svg\b/i);
      assert.match(unavailable, /&lt;img/);
      assert.match(broken, /&lt;svg/);
    });

- [ ] **Step 2: Run the focused test to prove it is red**

Run:

    node --test test/markdown-rendering.test.js

Expected: FAIL with Cannot find module '../public/markdown-rendering'. Do not create the module before observing this failure.

## Task 2: Implement the isolated safe renderer

**Files:**

- Create: public/markdown-rendering.js
- Test: test/markdown-rendering.test.js

- [ ] **Step 1: Implement the smallest module that satisfies the tests**

Create public/markdown-rendering.js with this complete implementation. The Renderer instance is created per call, so it does not mutate marked globally. Only output from KaTeX is reintroduced as markup; all user-originated HTML and fallback formula text are escaped.

    (function (root, factory) {
      const api = factory();
      if (typeof module === 'object' && module.exports) module.exports = api;
      if (root) root.MarkdownRendering = api;
    }(typeof window !== 'undefined' ? window : undefined, function () {
      let renderSequence = 0;

      function text(value) {
        return value == null ? '' : String(value);
      }

      function escapeHtml(value) {
        return text(value).replace(/[&<>"']/g, char => ({
          '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
        }[char]));
      }

      function allowedHref(value) {
        const candidate = text(value).trim();
        if (!candidate) return null;
        try {
          const parsed = new URL(candidate);
          return ['http:', 'https:', 'mailto:'].includes(parsed.protocol) ? parsed.href : null;
        } catch (error) {
          return null;
        }
      }

      function createSafeMarkedRenderer(marked) {
        const renderer = new marked.Renderer();

        renderer.html = ({ raw, text: htmlText }) => escapeHtml(raw || htmlText);
        renderer.link = function ({ href, title, tokens }) {
          const label = this.parser.parseInline(tokens || []);
          const safeHref = allowedHref(href);
          if (!safeHref) return label;
          const safeTitle = title ? ' title="' + escapeHtml(title) + '"' : '';
          return '<a href="' + escapeHtml(safeHref) + '"' + safeTitle + '>' + label + '</a>';
        };
        renderer.image = function ({ text: altText }) {
          return escapeHtml(altText);
        };

        return renderer;
      }

      function createMarkdownRenderer({ getMarked, getKatex } = {}) {
        const readMarked = typeof getMarked === 'function' ? getMarked : () => null;
        const readKatex = typeof getKatex === 'function' ? getKatex : () => null;

        function renderFormula(formula, katex) {
          const tex = text(formula.tex).trim();
          try {
            if (katex && typeof katex.renderToString === 'function') {
              return katex.renderToString(tex, {
                displayMode: Boolean(formula.display),
                throwOnError: false,
                trust: false,
                maxExpand: 1000,
              });
            }
          } catch (error) {
            // The escaped source below is the visible, non-fatal fallback.
          }
          return escapeHtml(tex);
        }

        function render(value) {
          const formulas = [];
          const markerPrefix = '\uE000markdown-' + String(++renderSequence) + '-';
          const markerSuffix = '\uE001';
          const stash = (tex, display) => markerPrefix + String(formulas.push({ tex, display }) - 1) + markerSuffix;
          const source = text(value)
            .replace(/\$\$([\s\S]+?)\$\$/g, (_match, tex) => stash(tex, true))
            .replace(/\\\[([\s\S]+?)\\\]/g, (_match, tex) => stash(tex, true))
            .replace(/(?<!\\)\$([^\n$]+?)\$/g, (_match, tex) => stash(tex, false))
            .replace(/\\\(([\s\S]+?)\\\)/g, (_match, tex) => stash(tex, false));
          const marked = readMarked();
          let html;

          try {
            html = marked && typeof marked.parse === 'function' && typeof marked.Renderer === 'function'
              ? marked.parse(source, { renderer: createSafeMarkedRenderer(marked) })
              : '<pre>' + escapeHtml(source) + '</pre>';
          } catch (error) {
            html = '<pre>' + escapeHtml(source) + '</pre>';
          }

          const markerPattern = new RegExp(markerPrefix + '(\\d+)' + markerSuffix, 'g');
          const katex = readKatex();
          return html.replace(markerPattern, (_match, index) => {
            const formula = formulas[Number(index)];
            return formula ? renderFormula(formula, katex) : '';
          });
        }

        function renderInto(element, value) {
          if (element) element.innerHTML = render(value);
        }

        return { render, renderInto };
      }

      return { createMarkdownRenderer };
    }));

- [ ] **Step 2: Run the focused tests and fix only observed incompatibilities**

Run:

    node --test test/markdown-rendering.test.js

Expected: all seven module-level tests PASS. If marked normalizes an HTTPS URL differently, update only the expected assertion to the normalized safe href; do not loosen the accepted protocols or re-enable images / raw HTML.

- [ ] **Step 3: Commit the tested renderer and its regression tests**

    git add -- public/markdown-rendering.js test/markdown-rendering.test.js
    git diff --cached --check
    git commit -m "feat: harden markdown rendering"

Expected: one commit containing only the new renderer and its security tests.

## Task 3: Route the application through the safe renderer

**Files:**

- Modify: public/index.html
- Modify: public/app.js
- Modify: test/markdown-rendering.test.js

- [ ] **Step 1: Add a red page-wiring regression test**

Append this test:

    test('the page loads and delegates every rich-text view through the safe renderer', () => {
      const root = path.resolve(__dirname, '..');
      const index = fs.readFileSync(path.join(root, 'public', 'index.html'), 'utf8');
      const app = fs.readFileSync(path.join(root, 'public', 'app.js'), 'utf8');
      const renderMd = app.match(/function\s+renderMd\s*\([^)]*\)\s*\{[\s\S]*?\n\}/m);

      assert.ok(renderMd, 'expected renderMd function');
      assert.match(index, /<script\s+src=["']markdown-rendering\.js["']><\/script>\s*<script\s+src=["']ingest-rendering\.js["']><\/script>\s*<script\s+src=["']app\.js["']><\/script>/);
      assert.match(app, /const\s+markdownRenderer\s*=\s*window\.MarkdownRendering\.createMarkdownRenderer\(\{[\s\S]*?getMarked\s*:\s*\(\)\s*=>\s*window\.marked[\s\S]*?getKatex\s*:\s*\(\)\s*=>\s*window\.katex[\s\S]*?}\s*\)/);
      assert.match(renderMd[0], /markdownRenderer\.renderInto\(el,\s*text\)/);
      assert.doesNotMatch(renderMd[0], /marked\.parse|katex\.renderToString|innerHTML/);

      for (const selector of ['#notePreview', '#explainerView', '#transView', '.sel-pop-trans']) {
        assert.match(app, new RegExp('renderMd\\([^\\n]*' + selector.replace(/[.*+?^()|[\]\\]/g, '\\$&')));
      }
    });

- [ ] **Step 2: Run the focused test to prove the wiring assertion is red**

Run:

    node --test test/markdown-rendering.test.js

Expected: the module tests pass and the wiring test fails because markdown-rendering.js is not loaded and renderMd still owns parsing.

- [ ] **Step 3: Make the minimal HTML and application edits**

In public/index.html, place markdown-rendering.js immediately before ingest-rendering.js and app.js:

    <script src="paper-titles.js"></script>
    <script src="ndjson.js"></script>
    <script src="markdown-rendering.js"></script>
    <script src="ingest-rendering.js"></script>
    <script src="app.js"></script>

In public/app.js, remove the direct md helper and replace the current formula-parsing renderMd body immediately after esc with:

    const markdownRenderer = window.MarkdownRendering.createMarkdownRenderer({
      getMarked: () => window.marked,
      getKatex: () => window.katex,
    });

    function renderMd(el, text) {
      markdownRenderer.renderInto(el, text);
    }

Keep all existing renderMd call sites unchanged.

- [ ] **Step 4: Verify focused and full JavaScript suites**

Run:

    node --test test/markdown-rendering.test.js
    npm.cmd test

Expected: both commands exit with status 0.

- [ ] **Step 5: Commit the page wiring**

    git add -- public/index.html public/app.js test/markdown-rendering.test.js
    git diff --cached --check
    git commit -m "fix: use safe markdown renderer"

Expected: one commit containing only the script ordering, delegate, and wiring regression test.

## Task 4: Verify the completed behavior in tests and a browser

**Files:** Verify the four files above; do not edit AGENTS.md.

- [ ] **Step 1: Run repository test suites**

Run:

    npm.cmd test
    .\.venv\Scripts\python.exe -m unittest discover -s test -p "test_*.py"

Expected: both commands exit with status 0.

- [ ] **Step 2: Run a contained browser smoke test**

Start the local app:

    npm.cmd start

Expected: output includes http://localhost:5173 (or use $env:PORT=5174; npm.cmd start if 5173 is busy).

In the browser, temporarily preview this exact note text:

    # Safe title

    [safe link](https://example.com) and $x^2$.

    <img src=x onerror=alert(1)>
    ![tracking](https://tracker.example/pixel.png)
    [bad](javascript:alert(1))

Verify the heading, HTTPS link, and formula render; no img element is created; raw HTML is literal; image is only alt text; dangerous link is plain text. Restore the original note text and save it before ending the check. Stop the temporary server after verification.

- [ ] **Step 3: Inspect the final diff and repository state**

Run:

    git diff 2f9510a..HEAD --check
    git status --short

Expected: no whitespace errors. Preserve the user's pre-existing AGENTS.md modification without staging, editing, or reverting it.
