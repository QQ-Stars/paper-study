const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const css = fs.readFileSync(path.resolve(__dirname, '..', 'public', 'spatial.css'), 'utf8');

function selectorPreludes(source) {
  const clean = source.replace(/\/\*[\s\S]*?\*\//g, '');
  const selectors = [];
  let tokenStart = 0;
  for (let index = 0; index < clean.length; index += 1) {
    const character = clean[index];
    if (character === '{') {
      const prelude = clean.slice(tokenStart, index).trim();
      if (prelude && !prelude.startsWith('@')) {
        selectors.push(...prelude.split(',').map(selector => selector.trim()).filter(Boolean));
      }
      tokenStart = index + 1;
    } else if (character === '}') {
      tokenStart = index + 1;
    }
  }
  return selectors;
}

function atRuleBody(source, header) {
  const headerIndex = source.indexOf(header);
  assert.ok(headerIndex >= 0, `missing ${header}`);
  const open = source.indexOf('{', headerIndex);
  let depth = 1;
  for (let index = open + 1; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1;
    if (source[index] === '}') depth -= 1;
    if (depth === 0) return source.slice(open + 1, index);
  }
  assert.fail(`unclosed ${header}`);
}

function ruleBody(source, selector) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = source.match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  assert.ok(match, `missing rule ${selector}`);
  return match[1];
}

function themeHex(theme, token) {
  const block = css.match(new RegExp(`data-theme="${theme}"[^\\{]*\\{([^}]*)\\}`, 's'));
  assert.ok(block, `missing ${theme} theme block`);
  const value = block[1].match(new RegExp(`${token}:\\s*(#[0-9a-f]{6})`, 'i'));
  assert.ok(value, `missing ${token} in ${theme}`);
  return value[1];
}

function relativeLuminance(hex) {
  const channels = hex.slice(1).match(/../g).map(channel => parseInt(channel, 16) / 255);
  const linear = channels.map(channel => (
    channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
  ));
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function contrastRatio(first, second) {
  const a = relativeLuminance(first);
  const b = relativeLuminance(second);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

test('every spatial selector is positively scoped', () => {
  const selectors = selectorPreludes(css);
  assert.ok(selectors.length > 25);
  for (const selector of selectors) {
    assert.ok(
      selector.startsWith('html[data-ui-style="spatial"]'),
      `unscoped selector: ${selector}`,
    );
  }
  assert.doesNotMatch(css, /(^|\n)\s*:root\b/);
});

test('dark and light themes have emerald role ledgers', () => {
  assert.match(css, /data-theme="dark"[^\{]*\{[^}]*--sp-accent:\s*#2fe586/is);
  assert.match(css, /data-theme="light"[^\{]*\{[^}]*--sp-accent:\s*#08944f/is);
  for (const token of ['--sp-bg', '--sp-surface', '--sp-text', '--sp-muted', '--sp-border', '--sp-accent-fg', '--sp-danger', '--sp-warning']) {
    assert.ok(css.includes(token), `missing ${token}`);
  }
  assert.match(css, /--primary:\s*var\(--sp-accent-strong\)/);
  assert.match(css, /--ink:\s*var\(--sp-text\)/);
  for (const bridge of [
    '--accent', '--accent-ink', '--accent-soft', '--primary-2', '--ring',
    '--ok-soft', '--warn-soft', '--idle-soft', '--pdf-stage', '--star',
  ]) assert.ok(css.includes(`${bridge}:`), `missing legacy bridge ${bridge}`);
  assert.match(css, /--accent:\s*var\(--sp-accent-fg\)/);
  assert.match(css, /--accent-ink:\s*var\(--sp-accent-fg\)/);
  assert.match(css, /--ok:\s*var\(--sp-accent-fg\)/);
  assert.doesNotMatch(css, /--accent-ink:\s*var\(--sp-accent-ink\)/);
  assert.match(css, /--ring:\s*0 0 0 3px var\(--sp-accent\)/);
  assert.doesNotMatch(css, /(?:^|\n)\s*color:\s*var\(--sp-accent\)\s*;/);
  for (const theme of ['dark', 'light']) {
    assert.ok(
      contrastRatio(themeHex(theme, '--sp-accent'), themeHex(theme, '--sp-accent-ink')) >= 4.5,
      `${theme} filled emerald controls must meet normal-text contrast`,
    );
    assert.ok(
      contrastRatio(themeHex(theme, '--sp-accent-fg'), themeHex(theme, '--sp-surface-solid')) >= 4.5,
      `${theme} emerald foreground must meet normal-text contrast`,
    );
    assert.ok(
      contrastRatio(themeHex(theme, '--sp-accent-strong'), '#ffffff') >= 4.5,
      `${theme} legacy primary fills must remain readable with existing white text`,
    );
    for (const adjacent of ['--sp-bg', '--sp-surface-solid']) {
      assert.ok(
        contrastRatio(themeHex(theme, '--sp-accent'), themeHex(theme, adjacent)) >= 3,
        `${theme} focus and selection accent must remain visible against ${adjacent}`,
      );
    }
    assert.ok(
      contrastRatio(themeHex(theme, '--sp-border-strong'), themeHex(theme, '--sp-surface-solid')) >= 3,
      `${theme} strong control boundaries must meet non-text contrast`,
    );
  }
});

test('spatial styling covers every existing global work surface', () => {
  for (const selector of [
    '#rail', '#topbar', '#home', '#layout', '#review', '#manage', '#jobs',
    '#insights', '#settingsModal', '.table-card', '.modal-card', 'input', 'button',
  ]) assert.ok(css.includes(selector), `missing spatial treatment for ${selector}`);
  for (const selector of [
    'html[data-ui-style="spatial"] .paper-item.active .order-badge',
    'html[data-ui-style="spatial"] .panel-stub:hover .stub-ico',
  ]) {
    const declarations = ruleBody(css, selector);
    assert.match(declarations, /background:\s*var\(--sp-accent-strong\)/);
    assert.match(declarations, /color:\s*#fff/);
  }
});

test('the data stage has semantic depth without forbidden renderers', () => {
  assert.match(css, /\.spatial-layer/);
  assert.match(css, /\.layer-offset--2/);
  assert.match(css, /\.layer-offset-2/);
  assert.match(css, /\.layer-offset--4/);
  assert.match(css, /\.layer-offset-4/);
  assert.match(ruleBody(css, 'html[data-ui-style="spatial"] .spatial-layer-status'), /color:\s*var\(--sp-muted\)/);
  assert.match(ruleBody(css, 'html[data-ui-style="spatial"] .spatial-layer[aria-selected="true"] .spatial-layer-status'), /color:\s*var\(--sp-accent-fg\)/);
  assert.doesNotMatch(css, /@keyframes|canvas|webgl|three\.js|url\s*\(/i);
});
