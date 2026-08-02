const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const publicDirectory = path.resolve(__dirname, '..', 'public');
const workerFilename = path.join(publicDirectory, 'markdown-rendering-worker.js');
const workerImports = [
  'vendor/marked.min.js',
  'vendor/katex/katex.min.js',
  'markdown-rendering.js',
];

function readPublicSource(filename) {
  return fs.readFileSync(path.join(publicDirectory, filename), 'utf8');
}

function plainMessages(messages) {
  return messages.map(message => ({ ...message }));
}

function createWorkerRuntime() {
  const messages = [];
  const scope = {
    URL,
    postMessage(message) {
      messages.push(message);
    },
  };
  scope.self = scope;

  return { scope, context: vm.createContext(scope), messages };
}

function startWorker(runtime, source = readPublicSource('markdown-rendering-worker.js')) {
  vm.runInContext(source, runtime.context, { filename: workerFilename });
  return runtime;
}

function createRealWorker(source) {
  const runtime = createWorkerRuntime();
  const importCalls = [];
  let importError = null;

  runtime.scope.importScripts = (...sources) => {
    importCalls.push([...sources]);
    for (const filename of sources) {
      try {
        vm.runInContext(readPublicSource(filename), runtime.context, {
          filename: path.join(publicDirectory, filename),
        });
      } catch (error) {
        importError = error;
        throw error;
      }
    }
  };

  startWorker(runtime, source);

  return {
    ...runtime,
    importCalls,
    get importError() {
      return importError;
    },
  };
}

function createControlledWorker(configureImports) {
  const runtime = createWorkerRuntime();
  runtime.scope.importScripts = (...sources) => configureImports(runtime.scope, sources);
  return startWorker(runtime);
}

function assertErrorReply(worker, event, id, message) {
  const firstReply = worker.messages.length;
  assert.doesNotThrow(() => worker.scope.onmessage(event));
  assert.deepEqual(plainMessages(worker.messages.slice(firstReply)), [{ id, error: true }], message);
}

test('exports MarkdownRendering when the core runs in a self-only realm', () => {
  const scope = {};

  vm.runInNewContext(readPublicSource('markdown-rendering.js'), { self: scope }, {
    filename: 'markdown-rendering.js',
  });

  assert.equal(typeof scope.MarkdownRendering.createMarkdownRenderer, 'function');
});

test('worker loads real dependencies in one Worker realm and renders Markdown and KaTeX', () => {
  const worker = createRealWorker();

  assert.deepEqual(worker.importCalls, [workerImports]);
  assert.equal(worker.importError, null);
  assert.equal(typeof worker.scope.marked.parse, 'function');
  assert.equal(typeof worker.scope.katex.renderToString, 'function');
  assert.equal(typeof worker.scope.MarkdownRendering.createMarkdownRenderer, 'function');

  assert.doesNotThrow(() => worker.scope.onmessage({ data: { id: 17, text: '**safe** and $x$' } }));
  const [reply] = plainMessages(worker.messages);
  assert.equal(reply.id, 17);
  assert.match(reply.html, /<strong>safe<\/strong>/);
  assert.match(reply.html, /katex/);
});

test('worker rejects malformed messages before invoking the renderer', () => {
  let renderCalls = 0;
  const worker = createControlledWorker(scope => {
    scope.MarkdownRendering = {
      createMarkdownRenderer() {
        return {
          render() {
            renderCalls++;
            return '<p>unreachable</p>';
          },
        };
      },
    };
  });

  const cases = [
    { name: 'missing event', event: undefined, id: undefined },
    { name: 'missing id', event: { data: { text: 'source' } }, id: undefined },
    { name: 'non-integer id', event: { data: { id: 3.5, text: 'source' } }, id: 3.5 },
    { name: 'string id', event: { data: { id: '4', text: 'source' } }, id: '4' },
    { name: 'non-string text', event: { data: { id: 5, text: { not: 'text' } } }, id: 5 },
  ];

  for (const { name, event, id } of cases) {
    assertErrorReply(worker, event, id, name);
  }

  assert.equal(renderCalls, 0);
});

test('worker returns errors when initialization or rendering fails', () => {
  const cases = [
    {
      name: 'import failure',
      configureImports() {
        throw new Error('dependency unavailable');
      },
    },
    {
      name: 'factory unavailable',
      configureImports() {},
    },
    {
      name: 'factory failure',
      configureImports(scope) {
        scope.MarkdownRendering = {
          createMarkdownRenderer() {
            throw new Error('factory failure');
          },
        };
      },
    },
    {
      name: 'renderer failure',
      configureImports(scope) {
        scope.MarkdownRendering = {
          createMarkdownRenderer() {
            return {
              render() {
                throw new Error('renderer failure');
              },
            };
          },
        };
      },
    },
    {
      name: 'non-string renderer result',
      configureImports(scope) {
        scope.MarkdownRendering = {
          createMarkdownRenderer() {
            return { render() { return { html: 'not a string' }; } };
          },
        };
      },
    },
  ];

  for (const [index, { name, configureImports }] of cases.entries()) {
    const id = 20 + index;
    const worker = createControlledWorker(configureImports);
    assertErrorReply(worker, { data: { id, text: 'source' } }, id, name);
  }
});
