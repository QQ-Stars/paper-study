const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const publicDirectory = path.resolve(__dirname, '..', 'public');
const indexHtml = fs.readFileSync(path.join(publicDirectory, 'index.html'), 'utf8');
const appSource = fs.readFileSync(path.join(publicDirectory, 'app.js'), 'utf8');
const requiredScriptSources = [
  'ingest-rendering.js',
  'markdown-rendering-coordinator.js',
  'app.js',
];
const forbiddenScriptSources = [
  'markdown-rendering.js',
  'markdown-rendering-worker.js',
];

function readAttribute(attributes, name) {
  const attribute = new RegExp(
    "(?:^|\\s)" + name + "\\s*=\\s*(?:\"([^\"]*)\"|'([^']*)'|([^\\s\"'=<>]+))",
    'i',
  );
  const match = attributes.match(attribute);
  return match && (match[1] ?? match[2] ?? match[3]);
}

function hasAttribute(attributes, name) {
  return new RegExp('(?:^|\\s)' + name + '(?=\\s|=|$)', 'i').test(attributes);
}

function extractExternalScripts(html) {
  const scriptTags = /<script\b((?:"[^"]*"|'[^']*'|[^'">])*)>/gi;
  const scripts = [];
  let match;

  while ((match = scriptTags.exec(html))) {
    const attributes = match[1];
    const src = readAttribute(attributes, 'src');
    if (src) scripts.push({ src, attributes });
  }

  return scripts;
}

function assertPageScriptContract(html) {
  const scripts = extractExternalScripts(html);
  const scriptSources = scripts.map(script => script.src);

  for (const source of forbiddenScriptSources) {
    assert.equal(scriptSources.includes(source), false, 'the page must not load ' + source);
  }

  const requiredScripts = requiredScriptSources.map(source => {
    const matchingScripts = scripts.filter(script => script.src === source);
    assert.equal(matchingScripts.length, 1, 'expected exactly one classic external ' + source + ' script');
    return matchingScripts[0];
  });
  const firstRequiredIndex = scripts.indexOf(requiredScripts[0]);
  assert.deepEqual(
    scripts.slice(firstRequiredIndex, firstRequiredIndex + requiredScriptSources.length).map(script => script.src),
    requiredScriptSources,
    'ingest rendering, the markdown coordinator, and the app must be adjacent in that order',
  );

  for (const script of requiredScripts) {
    assert.equal(hasAttribute(script.attributes, 'async'), false, script.src + ' must not be async');
    assert.equal(hasAttribute(script.attributes, 'defer'), false, script.src + ' must not be deferred');
    assert.notEqual(
      readAttribute(script.attributes, 'type')?.trim().toLowerCase(),
      'module',
      script.src + ' must remain a classic script',
    );
  }
}

function assertAppRendererContract(source) {
  const factoryDeclarations = [...source.matchAll(
    /^[\t ]*const\s+markdownRenderer\s*=\s*window\s*\.\s*MarkdownRenderingCoordinator\s*\.\s*createMarkdownRenderCoordinator\s*\(\s*\)\s*;?[\t ]*$/gm,
  )];
  assert.equal(factoryDeclarations.length, 1, 'the app must create exactly one markdown renderer');

  const renderMd = source.match(
    /function\s+renderMd\s*\(\s*el\s*,\s*text\s*\)\s*\{([\s\S]*?)\}\s*(?=const\s+EMPTY_HTML\b)/,
  );
  assert.ok(renderMd, 'renderMd keeps its existing (el, text) signature');
  assert.ok(factoryDeclarations[0].index < renderMd.index, 'the app-level renderer is created before renderMd');

  const renderMdBody = renderMd[1];
  assert.match(
    renderMdBody,
    /^\s*return\s+markdownRenderer\s*\.\s*renderInto\s*\(\s*el\s*,\s*text\s*\)\s*;?\s*$/,
  );
  assert.doesNotMatch(renderMdBody, /\b(?:marked|katex|innerHTML)\b/i);
  assert.doesNotMatch(source, /\b(?:const|let|var)\s+md\s*=|\bfunction\s+md\s*\(/);
}

test('the page loads adjacent classic markdown scripts in the required order', () => {
  assertPageScriptContract(indexHtml);
});

test('the semantic page script check tolerates classic external tag formatting', () => {
  const formattedScripts = [
    '<script integrity="sha256-example" nonce="one" src=\'ingest-rendering.js\'></script>',
    '<script src = "markdown-rendering-coordinator.js" integrity=\'sha256-example\'></script>',
    '<script nonce="two" src=\'app.js\'></script>',
  ].join('\n');

  assert.doesNotThrow(() => assertPageScriptContract(formattedScripts));
});

test('the semantic page script check rejects a Worker URL mutation', () => {
  const workerUrlPage = indexHtml.replace(
    'markdown-rendering-coordinator.js',
    'markdown-rendering-worker.js',
  );
  assert.notEqual(workerUrlPage, indexHtml);
  assert.throws(() => assertPageScriptContract(workerUrlPage), /markdown-rendering-worker\.js/);
});

test('the application-level markdown renderer delegates every renderMd call to the coordinator', () => {
  assertAppRendererContract(appSource);
});
