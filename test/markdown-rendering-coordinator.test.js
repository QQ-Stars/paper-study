const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const coordinatorApi = require('../public/markdown-rendering-coordinator.js');
const { createMarkdownRenderCoordinator } = coordinatorApi;
const coordinatorFilename = path.resolve(__dirname, '..', 'public', 'markdown-rendering-coordinator.js');

class FakeElement {
  constructor() {
    this._innerHTML = '<p>existing markup</p>';
    this._textContent = 'existing text';
    this.innerHTMLWrites = [];
    this.textContentWrites = [];
  }

  get innerHTML() {
    return this._innerHTML;
  }

  set innerHTML(value) {
    this.innerHTMLWrites.push(value);
    this._innerHTML = value;
  }

  get textContent() {
    return this._textContent;
  }

  set textContent(value) {
    this.textContentWrites.push(value);
    this._textContent = value;
  }
}

class FakeWorker {
  constructor({ postMessageError } = {}) {
    this.messages = [];
    this.onmessage = null;
    this.onerror = null;
    this.terminateCalls = 0;
    this.postMessageError = postMessageError;
  }

  postMessage(message) {
    if (this.postMessageError) throw this.postMessageError;
    this.messages.push(message);
  }

  terminate() {
    this.terminateCalls++;
  }

  deliver(data) {
    if (typeof this.onmessage === 'function') this.onmessage({ data });
  }

  fail(event = { message: 'worker failure' }) {
    if (typeof this.onerror === 'function') this.onerror(event);
  }
}

class ManualTimers {
  constructor() {
    this.nextId = 1;
    this.tasks = new Map();
    this.delays = [];
    this.cleared = [];
  }

  setTimeout(callback, delay) {
    const id = this.nextId++;
    this.tasks.set(id, callback);
    this.delays.push({ id, delay });
    return id;
  }

  clearTimeout(id) {
    this.cleared.push(id);
    this.tasks.delete(id);
  }

  fire(id) {
    const callback = this.tasks.get(id);
    if (callback) callback();
  }
}

function createWorkerFactory(workers, options = {}) {
  const urls = [];
  const factory = url => {
    urls.push(url);
    if (options.createError) throw options.createError;
    if (Object.hasOwn(options, 'returnValue')) return options.returnValue;

    const worker = new FakeWorker({ postMessageError: options.postMessageError });
    workers.push(worker);
    return worker;
  };
  factory.urls = urls;
  return factory;
}

function createCoordinator({ createWorker, timers = new ManualTimers(), timeoutMs, workerUrl } = {}) {
  const options = {
    createWorker,
    setTimeout: timers.setTimeout.bind(timers),
    clearTimeout: timers.clearTimeout.bind(timers),
  };
  if (timeoutMs !== undefined) options.timeoutMs = timeoutMs;
  if (workerUrl !== undefined) options.workerUrl = workerUrl;
  return {
    coordinator: createMarkdownRenderCoordinator(options),
    timers,
  };
}

function assertFallback(element, source) {
  assert.equal(element.textContent, source);
  assert.deepEqual(element.innerHTMLWrites, []);
  assert.equal(element.innerHTML, '<p>existing markup</p>');
}

function assertCleaned(worker, timers, timerId) {
  assert.equal(worker.terminateCalls, 1);
  assert.equal(worker.onmessage, null);
  assert.equal(worker.onerror, null);
  assert.ok(timers.cleared.includes(timerId));
}

test('attaches the coordinator API in a browser-style UMD realm', () => {
  const source = fs.readFileSync(coordinatorFilename, 'utf8');
  const browserScope = {};

  vm.runInNewContext(source, { self: browserScope }, { filename: coordinatorFilename });

  assert.equal(typeof browserScope.MarkdownRenderingCoordinator.createMarkdownRenderCoordinator, 'function');

  const withoutBrowserExport = source.replace(
    /\r?\n  if \(root\) root\.MarkdownRenderingCoordinator = api;/,
    '',
  );
  assert.notEqual(withoutBrowserExport, source);
  const mutatedScope = {};
  vm.runInNewContext(withoutBrowserExport, { self: mutatedScope }, { filename: coordinatorFilename });
  assert.equal(mutatedScope.MarkdownRenderingCoordinator, undefined);
});

test('exports only the coordinator factory and renders a matching Worker result', () => {
  assert.deepEqual(Object.keys(coordinatorApi), ['createMarkdownRenderCoordinator']);

  const workers = [];
  const factory = createWorkerFactory(workers);
  const { coordinator, timers } = createCoordinator({
    createWorker: factory,
    timeoutMs: 91,
  });
  const element = new FakeElement();
  const source = '**safe** $x$';

  assert.equal(coordinator.renderInto(element, source), element);
  assert.deepEqual(factory.urls, ['markdown-rendering-worker.js']);
  assert.equal(workers.length, 1);
  assert.equal(element.innerHTML, '<p>existing markup</p>');
  assert.equal(element.textContent, 'existing text');

  const worker = workers[0];
  const [request] = worker.messages;
  assert.equal(typeof request.id, 'number');
  assert.deepEqual(request, { id: request.id, text: source });
  assert.deepEqual(timers.delays, [{ id: 1, delay: 91 }]);

  worker.deliver({ id: request.id, html: '<p>rendered in Worker</p>' });

  assert.equal(element.innerHTML, '<p>rendered in Worker</p>');
  assert.deepEqual(element.innerHTMLWrites, ['<p>rendered in Worker</p>']);
  assert.equal(element.textContent, 'existing text');
  assertCleaned(worker, timers, 1);
});

test('uses a configured Worker URL while preserving the default URL elsewhere', () => {
  const workers = [];
  const factory = createWorkerFactory(workers);
  const { coordinator, timers } = createCoordinator({
    createWorker: factory,
    workerUrl: 'workers/isolated-markdown.js',
  });
  const element = new FakeElement();

  coordinator.renderInto(element, 'source');

  assert.deepEqual(factory.urls, ['workers/isolated-markdown.js']);
  workers[0].fail();
  assertFallback(element, 'source');
  assertCleaned(workers[0], timers, 1);
});

test('falls back to the exact raw source for Worker and reply failures', () => {
  const source = '<img src=x onerror=alert(1)> raw source';

  const constructionTimers = new ManualTimers();
  const construction = createCoordinator({
    createWorker: createWorkerFactory([], { createError: new Error('unavailable') }),
    timers: constructionTimers,
  });
  const constructionElement = new FakeElement();
  assert.doesNotThrow(() => construction.coordinator.renderInto(constructionElement, source));
  assertFallback(constructionElement, source);
  assert.deepEqual(constructionTimers.delays, []);

  const unavailableTimers = new ManualTimers();
  const unavailable = createCoordinator({
    createWorker: createWorkerFactory([], { returnValue: null }),
    timers: unavailableTimers,
  });
  const unavailableElement = new FakeElement();
  assert.doesNotThrow(() => unavailable.coordinator.renderInto(unavailableElement, source));
  assertFallback(unavailableElement, source);
  assert.deepEqual(unavailableTimers.delays, []);

  let invalidWorkerTerminations = 0;
  const invalidWorker = { terminate() { invalidWorkerTerminations++; } };
  const invalidTimers = new ManualTimers();
  const invalid = createCoordinator({
    createWorker: createWorkerFactory([], { returnValue: invalidWorker }),
    timers: invalidTimers,
  });
  const invalidElement = new FakeElement();
  assert.doesNotThrow(() => invalid.coordinator.renderInto(invalidElement, source));
  assertFallback(invalidElement, source);
  assert.equal(invalidWorkerTerminations, 1);
  assert.deepEqual(invalidTimers.delays, []);

  const postWorkers = [];
  const postTimers = new ManualTimers();
  const post = createCoordinator({
    createWorker: createWorkerFactory(postWorkers, { postMessageError: new Error('post failed') }),
    timers: postTimers,
  });
  const postElement = new FakeElement();
  post.coordinator.renderInto(postElement, source);
  assertFallback(postElement, source);
  assertCleaned(postWorkers[0], postTimers, 1);

  const errorWorkers = [];
  const errorTimers = new ManualTimers();
  const errored = createCoordinator({
    createWorker: createWorkerFactory(errorWorkers),
    timers: errorTimers,
  });
  const errorElement = new FakeElement();
  errored.coordinator.renderInto(errorElement, source);
  errorWorkers[0].fail();
  assertFallback(errorElement, source);
  assertCleaned(errorWorkers[0], errorTimers, 1);

  const malformedReplies = [
    () => undefined,
    () => ({}),
    request => ({ id: request.id + 1, html: '<p>wrong id</p>' }),
    request => ({ id: request.id, html: null }),
    request => ({ id: request.id, html: 42 }),
  ];
  for (const makeReply of malformedReplies) {
    const workers = [];
    const timers = new ManualTimers();
    const instance = createCoordinator({ createWorker: createWorkerFactory(workers), timers });
    const element = new FakeElement();
    instance.coordinator.renderInto(element, source);
    const worker = workers[0];
    const [request] = worker.messages;

    worker.deliver(makeReply(request));

    assertFallback(element, source);
    assertCleaned(worker, timers, 1);
  }

  const timeoutWorkers = [];
  const timeoutTimers = new ManualTimers();
  const timeout = createCoordinator({
    createWorker: createWorkerFactory(timeoutWorkers),
    timers: timeoutTimers,
    timeoutMs: 17,
  });
  const timeoutElement = new FakeElement();
  timeout.coordinator.renderInto(timeoutElement, source);
  timeoutTimers.fire(1);
  assertFallback(timeoutElement, source);
  assertCleaned(timeoutWorkers[0], timeoutTimers, 1);
});

test('a newer request cancels the prior same-element job and ignores its captured stale callback', () => {
  const workers = [];
  const timers = new ManualTimers();
  const { coordinator } = createCoordinator({ createWorker: createWorkerFactory(workers), timers });
  const element = new FakeElement();

  coordinator.renderInto(element, 'first raw source');
  const firstWorker = workers[0];
  const firstRequest = firstWorker.messages[0];
  const staleMessageHandler = firstWorker.onmessage;

  coordinator.renderInto(element, 'second raw source');
  const secondWorker = workers[1];
  const secondRequest = secondWorker.messages[0];

  assertCleaned(firstWorker, timers, 1);
  assert.equal(element.innerHTML, '<p>existing markup</p>');
  assert.equal(element.textContent, 'existing text');

  assert.doesNotThrow(() => staleMessageHandler({
    data: { id: firstRequest.id, html: '<p>stale Worker result</p>' },
  }));
  assert.equal(element.innerHTML, '<p>existing markup</p>');
  assert.equal(element.textContent, 'existing text');

  secondWorker.deliver({ id: secondRequest.id, html: '<p>second Worker result</p>' });
  assert.equal(element.innerHTML, '<p>second Worker result</p>');
  assert.equal(element.textContent, 'existing text');
  assertCleaned(secondWorker, timers, 2);
});

test('different elements maintain independent active Worker jobs', () => {
  const workers = [];
  const timers = new ManualTimers();
  const { coordinator } = createCoordinator({ createWorker: createWorkerFactory(workers), timers });
  const firstElement = new FakeElement();
  const secondElement = new FakeElement();

  coordinator.renderInto(firstElement, 'first');
  coordinator.renderInto(secondElement, 'second');

  const [firstWorker, secondWorker] = workers;
  assert.notEqual(firstWorker, secondWorker);
  firstWorker.deliver({ id: firstWorker.messages[0].id, html: '<p>first result</p>' });
  assert.equal(firstElement.innerHTML, '<p>first result</p>');
  assert.equal(secondElement.innerHTML, '<p>existing markup</p>');
  assert.equal(secondWorker.terminateCalls, 0);

  secondWorker.deliver({ id: secondWorker.messages[0].id, html: '<p>second result</p>' });
  assert.equal(secondElement.innerHTML, '<p>second result</p>');
  assertCleaned(firstWorker, timers, 1);
  assertCleaned(secondWorker, timers, 2);
});

test('null and unstringifiable values fall back to empty text and invalid timeouts use 200ms', () => {
  const inputs = [
    null,
    undefined,
    { toString() { throw new Error('cannot stringify'); } },
  ];

  for (const input of inputs) {
    const element = new FakeElement();
    const { coordinator } = createCoordinator({
      createWorker: createWorkerFactory([], { createError: new Error('unavailable') }),
    });
    assert.equal(coordinator.renderInto(element, input), element);
    assertFallback(element, '');
  }

  const zeroWorkers = [];
  const zeroTimers = new ManualTimers();
  const zero = createCoordinator({
    createWorker: createWorkerFactory(zeroWorkers),
    timers: zeroTimers,
    timeoutMs: 0,
  });
  zero.coordinator.renderInto(new FakeElement(), 'source');
  assert.deepEqual(zeroTimers.delays, [{ id: 1, delay: 0 }]);

  const invalidTimeouts = [undefined, null, -1, NaN, Infinity, '50'];
  for (const timeoutMs of invalidTimeouts) {
    const workers = [];
    const timers = new ManualTimers();
    const { coordinator } = createCoordinator({
      createWorker: createWorkerFactory(workers),
      timers,
      timeoutMs,
    });
    coordinator.renderInto(new FakeElement(), 'source');
    assert.deepEqual(timers.delays, [{ id: 1, delay: 200 }]);
  }

  let factoryCalls = 0;
  const { coordinator } = createCoordinator({
    createWorker() { factoryCalls++; return new FakeWorker(); },
  });
  assert.equal(coordinator.renderInto(null, 'source'), null);
  assert.equal(coordinator.renderInto(0, 'source'), 0);
  assert.equal(factoryCalls, 0);
});
