const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const css = fs.readFileSync(path.resolve(__dirname, '..', 'public', 'spatial.css'), 'utf8');
const legacyCss = fs.readFileSync(path.resolve(__dirname, '..', 'public', 'style.css'), 'utf8');
const academicCss = fs.readFileSync(path.resolve(__dirname, '..', 'public', 'academic.css'), 'utf8');

function withoutComments(source) {
  return source.replace(/\/\*[\s\S]*?\*\//g, '');
}

function selectorPreludes(source) {
  const clean = withoutComments(source);
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
  const match = source.match(new RegExp(`(?:^|[},])\\s*${escaped}\\s*\\{([^}]*)\\}`, 'm'));
  assert.ok(match, `missing rule ${selector}`);
  return match[1];
}

function colorMixAlpha(declarations, token) {
  const escaped = token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = declarations.match(new RegExp(
    `background:\\s*color-mix\\(in srgb,\\s*var\\(${escaped}\\)\\s*(\\d+(?:\\.\\d+)?)%,\\s*transparent\\s*\\)`,
  ));
  assert.ok(match, `missing transparent ${token} color mix`);
  return Number(match[1]) / 100;
}

function themeBlock(theme) {
  const block = css.match(new RegExp(`data-theme="${theme}"[^\\{]*\\{([^}]*)\\}`, 's'));
  assert.ok(block, `missing ${theme} theme block`);
  return block[1];
}

function themeValue(theme, token) {
  const value = themeBlock(theme).match(new RegExp(`${token}:\\s*([^;]+);`, 'i'));
  assert.ok(value, `missing ${token} in ${theme}`);
  return value[1].trim();
}

function themeHex(theme, token) {
  const value = themeValue(theme, token);
  assert.match(value, /^#[0-9a-f]{6}$/i, `${token} in ${theme} must be a six-digit hex color`);
  return value;
}

function themeRgba(theme, token) {
  const value = themeValue(theme, token);
  const match = value.match(/^rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*([\d.]+)\s*\)$/i);
  assert.ok(match, `${token} in ${theme} must be rgba`);
  const channels = match.slice(1).map(Number);
  assert.ok(channels.slice(0, 3).every(channel => channel >= 0 && channel <= 255));
  assert.ok(channels[3] >= 0 && channels[3] <= 1);
  return channels;
}

function hexChannels(hex) {
  return hex.slice(1).match(/../g).map(channel => parseInt(channel, 16));
}

function relativeLuminance(channels) {
  const linear = channels.map(value => {
    const channel = value / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
}

function compositeRgbaOverHex(rgba, hex) {
  const background = hexChannels(hex);
  return rgba.slice(0, 3).map((channel, index) => (
    channel * rgba[3] + background[index] * (1 - rgba[3])
  ));
}

function channelContrastRatio(first, second) {
  const a = relativeLuminance(first);
  const b = relativeLuminance(second);
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

function contrastRatio(first, second) {
  return channelContrastRatio(hexChannels(first), hexChannels(second));
}

test('every spatial selector is positively scoped', () => {
  const selectors = selectorPreludes(css);
  assert.ok(selectors.length >= 180, `expected desktop system scale, found ${selectors.length}`);
  for (const selector of selectors) {
    assert.ok(
      selector.startsWith('html[data-ui-style="spatial"]'),
      `unscoped selector: ${selector}`,
    );
  }
  assert.doesNotMatch(css, /(^|\n)\s*:root\b/);
});

test('spatial blocks stay balanced after comments are removed', () => {
  let depth = 0;
  for (const character of withoutComments(css)) {
    if (character === '{') depth += 1;
    if (character === '}') depth -= 1;
    assert.ok(depth >= 0, 'a spatial block closes before it opens');
  }
  assert.equal(depth, 0, 'spatial blocks must finish at depth zero');
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

test('theme copy and semantic states remain readable on their rendered surfaces', () => {
  for (const theme of ['dark', 'light']) {
    for (const foreground of ['--sp-text', '--sp-muted']) {
      for (const background of ['--sp-bg', '--sp-surface-solid']) {
        assert.ok(
          contrastRatio(themeHex(theme, foreground), themeHex(theme, background)) >= 4.5,
          `${theme} ${foreground} must remain readable on ${background}`,
        );
      }
    }

    const surface = themeHex(theme, '--sp-surface-solid');
    for (const role of ['danger', 'warning']) {
      const foreground = themeHex(theme, `--sp-${role}`);
      assert.ok(
        contrastRatio(foreground, surface) >= 4.5,
        `${theme} ${role} foreground must remain readable on the solid surface`,
      );
      const softSurface = compositeRgbaOverHex(themeRgba(theme, `--sp-${role}-soft`), surface);
      assert.ok(
        channelContrastRatio(hexChannels(foreground), softSurface) >= 4.5,
        `${theme} ${role} foreground must remain readable on its composited soft surface`,
      );
    }
  }
});

test('actual legacy badges meet normal-text contrast in both themes', () => {
  const ccfA = ruleBody(legacyCss, '.ccf-A');
  assert.match(ccfA, /color:\s*var\(--accent-ink\)/);
  const ccfAlpha = colorMixAlpha(ccfA, '--accent');
  assert.equal(ccfAlpha, 0.18);

  const verified = ruleBody(legacyCss, '.vbadge.ok');
  assert.match(verified, /color:\s*var\(--ok\)/);
  const verifiedAlpha = colorMixAlpha(verified, '--ok');
  assert.equal(verifiedAlpha, 0.16);

  const order = ruleBody(academicCss, '.order-badge');
  assert.match(order, /background:\s*var\(--surface-3\)/);
  assert.match(order, /color:\s*var\(--ink-2\)/);

  const missing = ruleBody(legacyCss, '.vbadge.miss');
  assert.match(missing, /background:\s*var\(--surface-3\)/);
  assert.match(missing, /color:\s*var\(--ink-3\)/);

  assert.match(css, /--accent:\s*var\(--sp-accent-fg\)/);
  assert.match(css, /--accent-ink:\s*var\(--sp-accent-fg\)/);
  assert.match(css, /--ok:\s*var\(--sp-accent-fg\)/);
  assert.match(css, /--surface-3:\s*var\(--sp-surface-muted\)/);
  assert.match(css, /--ink-2:\s*var\(--sp-muted\)/);
  assert.match(css, /--ink-3:\s*var\(--sp-muted\)/);

  for (const theme of ['dark', 'light']) {
    const accentForeground = hexChannels(themeHex(theme, '--sp-accent-fg'));
    const surface = themeHex(theme, '--sp-surface-solid');
    for (const [badge, alpha] of [['ccf-A', ccfAlpha], ['vbadge.ok', verifiedAlpha]]) {
      const tint = compositeRgbaOverHex([...accentForeground, alpha], surface);
      assert.ok(
        channelContrastRatio(accentForeground, tint) >= 4.5,
        `${theme} ${badge} must remain readable on its actual tint`,
      );
    }

    const muted = themeHex(theme, '--sp-muted');
    const mutedSurface = themeHex(theme, '--sp-surface-muted');
    for (const badge of ['order-badge', 'vbadge.miss']) {
      assert.ok(
        contrastRatio(muted, mutedSurface) >= 4.5,
        `${theme} ${badge} must remain readable on surface-3`,
      );
    }
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
