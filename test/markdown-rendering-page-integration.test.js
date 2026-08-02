const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const publicDirectory = path.resolve(__dirname, '..', 'public');
const indexHtml = fs.readFileSync(path.join(publicDirectory, 'index.html'), 'utf8');
const appSource = fs.readFileSync(path.join(publicDirectory, 'app.js'), 'utf8');

test('the page loads the markdown coordinator between ingest rendering and the app', () => {
  assert.match(
    indexHtml,
    /<script src="ingest-rendering\.js"><\/script>\s*<script src="markdown-rendering-coordinator\.js"><\/script>\s*<script src="app\.js"><\/script>/,
  );
  assert.doesNotMatch(indexHtml, /<script\b[^>]*\bsrc=["']markdown-rendering\.js["'][^>]*><\/script>/);
});

test('the application-level markdown renderer delegates every renderMd call to the coordinator', () => {
  const coordinatorDeclaration = 'const markdownRenderer = window.MarkdownRenderingCoordinator.createMarkdownRenderCoordinator();';
  assert.equal(appSource.split(coordinatorDeclaration).length - 1, 1);
  assert.match(appSource, /^const markdownRenderer = window\.MarkdownRenderingCoordinator\.createMarkdownRenderCoordinator\(\);$/m);

  const renderMd = appSource.match(/function renderMd\(el, text\) \{([\s\S]*?)\r?\n\}\r?\nconst EMPTY_HTML/);
  assert.ok(renderMd, 'renderMd keeps its existing (el, text) signature');

  const renderMdBody = renderMd[1];
  assert.match(renderMdBody, /^\s*return markdownRenderer\.renderInto\(el, text\);\s*$/);
  assert.doesNotMatch(renderMdBody, /\b(?:marked|katex|innerHTML)\b/i);
  assert.doesNotMatch(appSource, /^const md\s*=/m);
});
