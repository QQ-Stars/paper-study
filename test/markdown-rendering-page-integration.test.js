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
const pageUrl = 'https://paper-study.test/';
const classicJavaScriptTypes = new Set([
  'text/javascript',
  'application/javascript',
  'text/ecmascript',
  'application/ecmascript',
]);

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

function normalizeScriptPath(source) {
  return new URL(source, pageUrl).pathname.replace(/^\/+/, '');
}

function isClassicJavaScriptType(type) {
  const normalizedType = type == null ? '' : type.trim().toLowerCase();
  return normalizedType === '' || classicJavaScriptTypes.has(normalizedType);
}

function extractExternalScripts(html) {
  const scriptTags = /<script\b((?:"[^"]*"|'[^']*'|[^'">])*)>/gi;
  const scripts = [];
  let match;

  while ((match = scriptTags.exec(html))) {
    const attributes = match[1];
    const src = readAttribute(attributes, 'src');
    if (src) {
      const path = normalizeScriptPath(src);
      scripts.push({ src, path, basename: path.slice(path.lastIndexOf('/') + 1), attributes });
    }
  }

  return scripts;
}

function assertPageScriptContract(html) {
  const scripts = extractExternalScripts(html);
  const scriptBasenames = scripts.map(script => script.basename);

  for (const source of forbiddenScriptSources) {
    assert.equal(scriptBasenames.includes(source), false, 'the page must not load ' + source);
  }

  const requiredScripts = requiredScriptSources.map(source => {
    const matchingScripts = scripts.filter(script => script.basename === source);
    assert.equal(matchingScripts.length, 1, 'expected exactly one classic external ' + source + ' script');
    return matchingScripts[0];
  });
  const firstRequiredIndex = scripts.indexOf(requiredScripts[0]);
  assert.deepEqual(
    scripts.slice(firstRequiredIndex, firstRequiredIndex + requiredScriptSources.length).map(script => script.basename),
    requiredScriptSources,
    'ingest rendering, the markdown coordinator, and the app must be adjacent in that order',
  );

  for (const script of requiredScripts) {
    assert.equal(hasAttribute(script.attributes, 'async'), false, script.src + ' must not be async');
    assert.equal(hasAttribute(script.attributes, 'defer'), false, script.src + ' must not be deferred');
    assert.equal(hasAttribute(script.attributes, 'nomodule'), false, script.src + ' must not be nomodule');
    assert.equal(
      isClassicJavaScriptType(readAttribute(script.attributes, 'type')),
      true,
      script.src + ' must use a classic JavaScript type',
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
    '<script integrity="sha256-example" type=" text/javascript " nonce="one" src=\'ingest-rendering.js\'></script>',
    '<script src = "markdown-rendering-coordinator.js" integrity=\'sha256-example\' type="application/javascript"></script>',
    '<script nonce="two" type=\'application/ecmascript\' src=\'app.js\'></script>',
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

test('the semantic page script check rejects unsafe sources and non-classic script modes', () => {
  const appendScriptAfterApp = script => indexHtml.replace(
    '<script src="app.js"></script>',
    '<script src="app.js"></script>\n' + script,
  );
  const coordinatorScript = '<script src="markdown-rendering-coordinator.js"></script>';
  const appScript = '<script src="app.js"></script>';
  const mutations = [
    ['leading-slash Worker source', appendScriptAfterApp('<script src="/markdown-rendering-worker.js"></script>')],
    ['Worker source with query and hash', appendScriptAfterApp('<script src="/assets/markdown-rendering-worker.js?v=1#fragment"></script>')],
    ['core source with query', appendScriptAfterApp('<script src="markdown-rendering.js?v=1"></script>')],
    ['core source with hash', appendScriptAfterApp('<script src="/assets/markdown-rendering.js#fragment"></script>')],
    ['coordinator module type', indexHtml.replace(coordinatorScript, '<script type="module" src="markdown-rendering-coordinator.js"></script>')],
    ['coordinator text/plain type', indexHtml.replace(coordinatorScript, '<script type="text/plain" src="markdown-rendering-coordinator.js"></script>')],
    ['coordinator JSON type', indexHtml.replace(coordinatorScript, '<script type="application/json" src="markdown-rendering-coordinator.js"></script>')],
    ['app async mode', indexHtml.replace(appScript, '<script async src="app.js"></script>')],
    ['app defer mode', indexHtml.replace(appScript, '<script defer src="app.js"></script>')],
    ['app nomodule mode', indexHtml.replace(appScript, '<script nomodule src="app.js"></script>')],
  ];

  for (const [name, mutatedHtml] of mutations) {
    assert.notEqual(mutatedHtml, indexHtml, name);
    assert.throws(() => assertPageScriptContract(mutatedHtml), name);
  }
});

test('the application-level markdown renderer delegates every renderMd call to the coordinator', () => {
  assertAppRendererContract(appSource);
});
