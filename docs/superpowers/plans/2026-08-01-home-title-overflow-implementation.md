# Home Paper Title Overflow Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent long bilingual paper titles in the home overview table from shifting left or protruding while preserving the compact one-line-per-language layout.

**Architecture:** Wrap the optional semantic score, favorite control, and existing title stack in one home-only Flex container. Let the badges retain their intrinsic width while the title stack consumes only the remaining width with `min-width: 0`; keep both title lines ellipsized. Add a zero-dependency source contract test for the required markup and CSS, then verify geometry and appearance in the real browser.

**Tech Stack:** Vanilla HTML/CSS/JavaScript, Node.js CommonJS, Node test runner, existing in-app browser and Playwright-compatible controls.

---

## File Map

- Create `test/home-title-layout.test.js`: regression contract for the home row wrapper, Flex sizing, and two-line ellipsis rules.
- Modify `public/app.js:690-704`: add the home-only layout wrapper without changing title content or event targets.
- Modify `public/style.css:155-160,199-209,425-438`: constrain the title stack to remaining space while preserving management and sidebar title behavior.

## Constraints

- Keep English and Chinese titles on separate single lines.
- Preserve the complete title in data and in the existing table-cell `title` attribute.
- Preserve `.fav-star`, `.sem-score`, and `.paper-title-stack` class names and event behavior.
- Do not modify backend APIs, database data, reading views, management views, or review cards.
- Do not stage or alter the user's existing `AGENTS.md` change.

---

### Task 1: Add the regression contract and Flex layout

**Files:**
- Create: `test/home-title-layout.test.js`
- Modify: `public/app.js:690-704`
- Modify: `public/style.css:155-160,199-209,425-438`

- [ ] **Step 1: Write the failing markup and CSS contract tests**

Create `test/home-title-layout.test.js`:

```js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const appSource = fs.readFileSync(path.join(__dirname, '..', 'public', 'app.js'), 'utf8');
const styleSource = fs.readFileSync(path.join(__dirname, '..', 'public', 'style.css'), 'utf8');

function cssRuleBody(selector) {
  const css = styleSource.replace(/\/\*[\s\S]*?\*\//g, '');
  const block = css.split('}').find(candidate => {
    const brace = candidate.indexOf('{');
    if (brace < 0) return false;
    return candidate.slice(0, brace).split(',').map(value => value.trim()).includes(selector);
  });
  assert.ok(block, `missing CSS rule for ${selector}`);
  return block.slice(block.indexOf('{') + 1);
}

test('home title cell groups badges and the title stack in one layout wrapper', () => {
  const rowSource = appSource.slice(
    appSource.indexOf('function rowHTML('),
    appSource.indexOf('// ====== 阅读视图', appSource.indexOf('function rowHTML('))
  );

  assert.match(
    rowSource,
    /<td class="ht-title"[^>]*><span class="ht-title-content">\$\{semScoreBadge\(p\.id\)\}<span class="fav-star/
  );
  assert.match(rowSource, /\$\{titleMarkup\(p\)\}<\/span><\/td>/);
});

test('home title layout reserves badge width and truncates both title lines', () => {
  const layout = cssRuleBody('.ht-title-content');
  assert.match(layout, /display:flex/);
  assert.match(layout, /width:100%/);
  assert.match(layout, /min-width:0/);

  const fixedScore = cssRuleBody('.ht-title-content>.sem-score');
  const fixedFavorite = cssRuleBody('.ht-title-content>.fav-star');
  assert.match(fixedScore, /flex:0 0 auto/);
  assert.match(fixedFavorite, /flex:0 0 auto/);

  const stack = cssRuleBody('.ht-title .paper-title-stack');
  assert.match(stack, /flex:1/);
  assert.match(stack, /min-width:0/);
  assert.doesNotMatch(stack, /max-width:100%/);

  for (const selector of ['.ht-title .paper-title-primary', '.ht-title .paper-title-secondary']) {
    const line = cssRuleBody(selector);
    assert.match(line, /overflow:hidden/);
    assert.match(line, /text-overflow:ellipsis/);
    assert.match(line, /white-space:nowrap/);
  }
});
```

- [ ] **Step 2: Run the focused test and verify the red state**

Run:

```powershell
node --test test/home-title-layout.test.js
```

Expected: FAIL because `rowHTML` has no `.ht-title-content` wrapper and `public/style.css` has no matching Flex rule.

- [ ] **Step 3: Add the home-only layout wrapper**

In `public/app.js`, replace the title cell in `rowHTML` with:

```js
    <td class="ht-title" title="${esc(titleSearch(p))}"><span class="ht-title-content">${semScoreBadge(p.id)}<span class="fav-star ${p.favorite ? 'on' : ''}" data-id="${p.id}" title="${p.favorite ? '取消收藏' : '收藏'}">${p.favorite ? '★' : '☆'}</span>${titleMarkup(p)}</span></td>
```

The wrapper is presentation-only. The `.fav-star` node and `data-id` remain unchanged, so the existing click handler continues to work.

- [ ] **Step 4: Constrain the title stack to the remaining width**

In `public/style.css`, split the current shared title-stack selector so management rows retain their existing inline behavior, then add the home rules:

```css
.ht-title .paper-title-stack{flex:1;min-width:0}
.m-item-title .paper-title-stack{display:inline-flex;vertical-align:middle;max-width:100%}
.ht-title .paper-title-primary,.ht-title .paper-title-secondary,.pi-title .paper-title-primary,.m-item-title .paper-title-primary{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
```

Immediately after `#homeTable tbody td.ht-title`, add:

```css
.ht-title-content{display:flex;align-items:center;width:100%;min-width:0}
.ht-title-content>.sem-score,.ht-title-content>.fav-star{flex:0 0 auto}
```

Keep the existing `.ht-title .fav-star{margin-right:7px}` and `.sem-score` margins. They provide the spacing while the new Flex rules provide sizing.

- [ ] **Step 5: Run the focused test and verify the green state**

Run:

```powershell
node --test test/home-title-layout.test.js
```

Expected: 2 tests PASS.

- [ ] **Step 6: Run the complete Node test suite**

Run:

```powershell
npm test
```

Expected: all Node tests PASS with no new failures.

- [ ] **Step 7: Inspect the scoped diff and commit the fix**

Run:

```powershell
git diff --check
git diff -- public/app.js public/style.css test/home-title-layout.test.js
git status --short
```

Expected: only the three task files plus the pre-existing unstaged `AGENTS.md` change are shown; `git diff --check` emits no errors.

Commit only the task files:

```powershell
git add -- public/app.js public/style.css test/home-title-layout.test.js
git commit -m "fix: contain long titles in home table"
```

---

### Task 2: Verify the result in the real research workspace

**Files:**
- Verify only: `public/app.js`, `public/style.css`

- [ ] **Step 1: Start the application on an available local port**

Run:

```powershell
npm start
```

Expected output includes `打开: http://localhost:5173`. If `5173` is occupied, use `$env:PORT=5174; npm start` and verify at `http://localhost:5174`.

- [ ] **Step 2: Verify desktop geometry with the reported long title**

At a desktop viewport around `1440 x 900`, open the home overview and locate:

`Improving Factuality in Large Language Models via Decoding-Time Hallucinatory and Truthful Comparators`

Use browser geometry inspection to verify:

```js
const row = [...document.querySelectorAll('#homeBody tr')].find(item =>
  item.textContent.includes('Improving Factuality in Large Language Models')
);
const cell = row.querySelector('.ht-title').getBoundingClientRect();
const layout = row.querySelector('.ht-title-content').getBoundingClientRect();
const favorite = row.querySelector('.fav-star').getBoundingClientRect();
const stack = row.querySelector('.paper-title-stack').getBoundingClientRect();
const primary = row.querySelector('.paper-title-primary');
const secondary = row.querySelector('.paper-title-secondary');
const primaryStyle = getComputedStyle(primary);
const secondaryStyle = secondary ? getComputedStyle(secondary) : null;
({
  layoutInsideCell: layout.left >= cell.left - 1 && layout.right <= cell.right + 1,
  titleAfterFavorite: stack.left >= favorite.right - 1,
  primarySingleLine: primaryStyle.whiteSpace === 'nowrap' && primary.clientHeight <= parseFloat(primaryStyle.lineHeight) + 1,
  secondarySingleLine: !secondary || (secondaryStyle.whiteSpace === 'nowrap' && secondary.clientHeight <= parseFloat(secondaryStyle.lineHeight) + 1)
});
```

Expected: all four values are `true`. Capture a screenshot showing the long row and its neighboring rows aligned.

- [ ] **Step 3: Verify the preserved controls without mutating library data**

Hover the long-title row and confirm its existing `.fav-star` becomes visible without changing the title position. Inspect the row DOM to confirm the favorite node remains a direct child of `.ht-title-content`, before `.paper-title-stack`. The focused automated test already verifies that an optional `.sem-score` is also a fixed-width child of this container.

Expected: hover styling does not change row height or title geometry, and no favorite or paper data is changed during verification.

- [ ] **Step 4: Verify narrow layout and both themes**

At a viewport around `900 x 800`, verify the table remains horizontally scrollable and the long title stays inside its title cell without covering the venue column. Repeat the desktop row check in both the current theme and the alternate theme using the existing theme control, then restore the user's original theme.

Expected: no incoherent overlap, no title protrusion, no unexpected row-height increase, and visible ellipses for clipped English and Chinese lines.

- [ ] **Step 5: Finish verification**

Stop the local server, run `git status --short`, and confirm browser verification created no tracked changes. Leave the pre-existing `AGENTS.md` modification untouched.
