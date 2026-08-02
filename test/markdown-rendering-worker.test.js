const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const publicDirectory = path.resolve(__dirname, '..', 'public');

function readPublicSource(filename) {
  return fs.readFileSync(path.join(publicDirectory, filename), 'utf8');
}

function createFakeWorkerScope(render) {
  const importCalls = [];
  const messages = [];
  let callbacksVerified = false;
  const scope = {
    marked: { name: 'marked' },
    katex: { name: 'katex' },
  };

  scope.importScripts = (...sources) => {
    importCalls.push(sources);
    scope.MarkdownRendering = {
      createMarkdownRenderer(options) {
        assert.equal(options.getMarked(), scope.marked);
        assert.equal(options.getKatex(), scope.katex);
        callbacksVerified = true;
        return { render };
      },
    };
  };
  scope.postMessage = message => messages.push(message);

  return {
    scope,
    importCalls,
    messages,
    get callbacksVerified() {
      return callbacksVerified;
    },
  };
}

test('exports MarkdownRendering when the core runs in a self-only realm', () => {
  const scope = {};

  vm.runInNewContext(readPublicSource('markdown-rendering.js'), { self: scope }, {
    filename: 'markdown-rendering.js',
  });

  assert.equal(typeof scope.MarkdownRendering.createMarkdownRenderer, 'function');
});

test('worker imports renderer dependencies and posts rendered HTML', () => {
  const workerSource = readPublicSource('markdown-rendering-worker.js');
  const worker = createFakeWorkerScope(text => {
    assert.equal(text, '**safe**');
    return '<p>**safe**</p>';
  });

  vm.runInNewContext(workerSource, { self: worker.scope }, {
    filename: 'markdown-rendering-worker.js',
  });

  assert.deepEqual(worker.importCalls, [[
    'vendor/marked.min.js',
    'vendor/katex/katex.min.js',
    'markdown-rendering.js',
  ]]);
  assert.equal(worker.callbacksVerified, true);
  assert.doesNotThrow(() => worker.scope.onmessage({ data: { id: 17, text: '**safe**' } }));
  assert.deepEqual(worker.messages.map(message => ({ ...message })), [{ id: 17, html: '<p>**safe**</p>' }]);
});

test('worker returns errors for invalid text and renderer failures', () => {
  const workerSource = readPublicSource('markdown-rendering-worker.js');
  let renderCalls = 0;
  const worker = createFakeWorkerScope(text => {
    renderCalls++;
    if (text === 'source') throw new Error('render failure');
    return '<p>unreachable</p>';
  });

  vm.runInNewContext(workerSource, { self: worker.scope }, {
    filename: 'markdown-rendering-worker.js',
  });

  assert.doesNotThrow(() => worker.scope.onmessage({ data: { id: 3, text: { not: 'text' } } }));
  assert.doesNotThrow(() => worker.scope.onmessage({ data: { id: 4, text: 'source' } }));
  assert.equal(renderCalls, 1);
  assert.deepEqual(worker.messages.map(message => ({ ...message })), [
    { id: 3, error: true },
    { id: 4, error: true },
  ]);
});
