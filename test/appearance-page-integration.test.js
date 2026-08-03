const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const publicDir = path.resolve(__dirname, '..', 'public');
const html = fs.readFileSync(path.join(publicDir, 'index.html'), 'utf8');
const app = fs.readFileSync(path.join(publicDir, 'app.js'), 'utf8');

function indexOfSource(source) {
  const expression = new RegExp(`<script\\b(?=[^>]*\\bsrc=["']${source.replace('.', '\\.') }["'])[^>]*>`, 'i');
  const match = expression.exec(html);
  assert.ok(match, `missing ${source}`);
  return { index: match.index, tag: match[0] };
}

test('the root has a script-free classic light fallback', () => {
  assert.match(html, /<html\b(?=[^>]*data-ui-style="classic")(?=[^>]*data-theme="light")[^>]*>/i);
});

test('appearance bootstraps synchronously before styles and application data', () => {
  const appearance = indexOfSource('appearance.js');
  assert.doesNotMatch(appearance.tag, /\b(?:async|defer|type\s*=\s*["']module["'])\b/i);
  assert.ok(appearance.index < html.indexOf('href="style.css"'));
  assert.ok(appearance.index < html.indexOf('src="app.js"'));
  assert.match(app, /fetch\(["']\/api\/papers["']\)/);
});

test('spatial CSS loads after the existing cascade', () => {
  const style = html.indexOf('href="style.css"');
  const academic = html.indexOf('href="academic.css"');
  const spatial = html.indexOf('href="spatial.css"');
  assert.ok(style >= 0 && style < academic && academic < spatial);
});

test('Settings exposes exactly four native appearance choices', () => {
  const fields = [...html.matchAll(/<input\b[^>]*data-appearance-field="(uiStyle|theme)"[^>]*value="(classic|spatial|light|dark)"[^>]*>/g)]
    .map(match => [match[1], match[2]]);
  assert.deepEqual(fields, [
    ['uiStyle', 'classic'],
    ['uiStyle', 'spatial'],
    ['theme', 'light'],
    ['theme', 'dark'],
  ]);
});

test('app.js is not an appearance writer', () => {
  assert.doesNotMatch(app, /function\s+(?:applyTheme|toggleTheme)\b/);
  assert.doesNotMatch(app, /setAttribute\(\s*["']data-(?:theme|ui-style)["']/);
  assert.doesNotMatch(app, /localStorage\.setItem\(\s*["'](?:theme|paperstudy\.uiStyle)["']/);
  assert.doesNotMatch(app, /#themeBtn["']\)\.onclick/);
  assert.equal([...app.matchAll(/addEventListener\(\s*["']paperstudy:appearancechange["']/g)].length, 1);
});

test('appearance switching redraws cached charts without a network request', () => {
  const handler = app.slice(app.indexOf('function handleAppearanceChange'), app.indexOf("document.addEventListener('paperstudy:appearancechange'"));
  assert.match(handler, /redrawInsightChartsFromCache\(\)/);
  assert.doesNotMatch(handler, /renderInsights\(\)|fetch\(/);
  assert.doesNotMatch(handler, /layoutPages\(/);
});

test('both citation fetch paths refresh the appearance redraw cache', () => {
  const assignments = [...app.matchAll(/insightCiteGraph\s*=\s*g\b/g)];
  assert.ok(assignments.length >= 2, 'renderInsights and buildCite both cache the latest graph');
});

test('server Settings payload never contains local appearance fields', () => {
  const saveSettings = app.slice(app.indexOf('async function saveSettings'), app.indexOf('function openSettingsModal'));
  assert.doesNotMatch(saveSettings, /uiStyle|paperstudy\.uiStyle|data-theme|\btheme\s*:/);
  assert.match(saveSettings, /try\s*\{/);
  assert.match(saveSettings, /catch\s*\(/);
});
