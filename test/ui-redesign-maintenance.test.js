const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const managePath = path.join(root, 'ui-redesign', 'src', 'components', 'ManagePage.tsx');
const readerPath = path.join(root, 'ui-redesign', 'src', 'components', 'ReaderPage.tsx');
const sidebarPath = path.join(root, 'ui-redesign', 'src', 'components', 'Sidebar.tsx');
const readerCssPath = path.join(root, 'ui-redesign', 'src', 'styles', 'reader.css');
const manageSource = fs.readFileSync(managePath, 'utf8').replace(/\r\n/g, '\n');
const readerSource = fs.readFileSync(readerPath, 'utf8').replace(/\r\n/g, '\n');
const sidebarSource = fs.readFileSync(sidebarPath, 'utf8').replace(/\r\n/g, '\n');
const readerCss = fs.readFileSync(readerCssPath, 'utf8').replace(/\r\n/g, '\n');

test('metadata enrichment does not report success after a failed NDJSON terminal event', () => {
  assert.match(manageSource, /event\.type === 'done' \|\| event\.type === 'result'/);
  assert.match(manageSource, /event\.ok === false/);
  assert.match(manageSource, /throw new Error\(terminalError/);
});

test('OCR and explainer batches forward the current per-task limit', () => {
  assert.match(manageSource, /const explainRequest = buildBatchTaskRequest\('explanations'/);
  assert.match(manageSource, /const ocrRequest = buildBatchTaskRequest\('ocrMarkdown'/);
  assert.match(manageSource, /await artifactApi\.explainBatch\(request\.request/);
  assert.match(manageSource, /await artifactApi\.ocrBatch\(request\.request/);
});

test('side-effecting batch failures refresh stale papers/status and notify the user', () => {
  for (const refresh of [
    'refreshTitleStatus',
    'refreshBatchStatus',
    'refreshOcrBatchStatus',
    'refreshEnrichStatus',
  ]) {
    assert.match(manageSource, new RegExp(`recoverBatchFailure\\(reloadPapers, ${refresh}\\)`));
  }
  assert.match(manageSource, /标题翻译失败：\$\{batchErrorMessage\(error\)\}/);
  assert.match(manageSource, /批量讲解失败：\$\{batchErrorMessage\(error\)\}/);
  assert.match(manageSource, /批量 PDF → Markdown 失败：\$\{batchErrorMessage\(error\)\}/);
  assert.match(manageSource, /元数据补全失败：\$\{batchErrorMessage\(error\)\}/);
});

test('batch task controls expose running state to assistive technology', () => {
  for (const [handler, busy] of [
    ['runNormVenues()', 'venueStream.state.running'],
    ['runExplainBatch()', 'batchStream.state.running'],
    ['runOcrBatch()', 'ocrBatchStream.state.running'],
    ['runDuplicateScan()', 'duplicateLoading'],
    ['runEnrich()', 'enrichStream.state.running'],
    ["runEmbed('missing')", 'embedStream.state.running'],
    ["runEmbed('all')", 'embedStream.state.running'],
  ]) {
    const handlerText = `onClick={() => void ${handler}}`;
    const handlerIndex = manageSource.indexOf(handlerText);
    assert.notEqual(handlerIndex, -1, `${handler} button should exist`);
    const buttonStart = manageSource.lastIndexOf('<button', handlerIndex);
    const buttonEnd = manageSource.indexOf('</button>', handlerIndex);
    const button = manageSource.slice(buttonStart, buttonEnd);
    assert.ok(button.includes(`disabled={${busy}}`) || button.includes(busy));
    assert.ok(button.includes(`aria-busy={${busy}}`), `${handler} button should expose aria-busy`);
  }
});

test('reader regeneration controls expose loading state and progress semantics', () => {
  for (const [kind, runningText] of [
    ['explainer', '讲解生成中…'],
    ['translation', '翻译生成中…'],
  ]) {
    const handlerText = `onClick={() => void regenerate('${kind}')}`;
    const handlerIndex = readerSource.indexOf(handlerText);
    assert.notEqual(handlerIndex, -1, `${kind} regeneration button should exist`);
    const buttonStart = readerSource.lastIndexOf('<button', handlerIndex);
    const buttonEnd = readerSource.indexOf('</button>', handlerIndex);
    const button = readerSource.slice(buttonStart, buttonEnd);
    assert.ok(
      button.includes(`aria-busy={regen.kind === '${kind}'}`),
      `${kind} regeneration button should expose aria-busy`,
    );
    assert.ok(button.includes(runningText), `${kind} regeneration button should name its loading state`);
  }
});

test('conditional deletion treats an HTTP-200 business failure as failed', () => {
  assert.match(manageSource, /const result = await libraryApi\.deletePaper\(deleteTargets\[i\]\.id\)/);
  assert.match(manageSource, /assertMutationOk\(result, '删除失败'\)/);
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
