const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const managePath = path.join(root, 'ui-redesign', 'src', 'components', 'ManagePage.tsx');
const sidebarPath = path.join(root, 'ui-redesign', 'src', 'components', 'Sidebar.tsx');
const readerCssPath = path.join(root, 'ui-redesign', 'src', 'styles', 'reader.css');
const manageSource = fs.readFileSync(managePath, 'utf8').replace(/\r\n/g, '\n');
const sidebarSource = fs.readFileSync(sidebarPath, 'utf8').replace(/\r\n/g, '\n');
const readerCss = fs.readFileSync(readerCssPath, 'utf8').replace(/\r\n/g, '\n');

test('metadata enrichment does not report success after a failed NDJSON terminal event', () => {
  assert.match(manageSource, /event\.type === 'done' \|\| event\.type === 'result'/);
  assert.match(manageSource, /event\.ok === false/);
  assert.match(manageSource, /throw new Error\(terminalError/);
});

test('existing OCR and explainer batches retain the safe limit of three', () => {
  assert.match(manageSource, /artifactApi\.explainBatch\(\s*\{ limit: 3 \}/);
  assert.match(manageSource, /artifactApi\.ocrBatch\(\s*\{ limit: 3 \}/);
});

test('collapsed sidebar navigation keeps stable accessible names', () => {
  assert.match(sidebarSource, /aria-label=\{item\.label\}/);
  assert.match(sidebarSource, /title=\{`\$\{item\.label\} · \$\{item\.hint\}`\}/);
});

test('reader mobile layout constrains grid width and exposes touch-sized controls', () => {
  assert.match(readerCss, /\.reader \{[\s\S]*?grid-template-columns: minmax\(0, 1fr\);[\s\S]*?min-width: 0;/);
  assert.match(readerCss, /\.reader__switch \{[\s\S]*?min-width: min\(16rem, 100%\);[\s\S]*?max-width: 100%;/);
  assert.match(readerCss, /@media \(max-width: 760px\) \{[\s\S]*?\.reader__topbar \.btn,[\s\S]*?\.reader__tab,[\s\S]*?\.pdfviewer__bar \.btn,[\s\S]*?min-height: 2\.75rem;/);
});
