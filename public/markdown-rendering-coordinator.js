(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.MarkdownRenderingCoordinator = api;
}(typeof self !== 'undefined' ? self : (typeof window !== 'undefined' ? window : undefined), function (root) {
  const workerUrl = 'markdown-rendering-worker.js';
  const defaultTimeoutMs = 200;

  function text(value) {
    if (value == null) return '';
    try {
      return String(value);
    } catch (error) {
      return '';
    }
  }

  function isElementKey(value) {
    return Boolean(value) && (typeof value === 'object' || typeof value === 'function');
  }

  function defaultCreateWorker(url) {
    const WorkerConstructor = root && root.Worker;
    return typeof WorkerConstructor === 'function' ? new WorkerConstructor(url) : null;
  }

  function defaultSetTimeout(callback, delay) {
    if (root && typeof root.setTimeout === 'function') return root.setTimeout(callback, delay);
    if (typeof setTimeout === 'function') return setTimeout(callback, delay);
    throw new Error('Timers are unavailable');
  }

  function defaultClearTimeout(timer) {
    if (root && typeof root.clearTimeout === 'function') return root.clearTimeout(timer);
    if (typeof clearTimeout === 'function') return clearTimeout(timer);
  }

  function validTimeout(value) {
    return typeof value === 'number' && Number.isFinite(value) && value >= 0;
  }

  function usableWorker(worker) {
    return Boolean(worker)
      && (typeof worker === 'object' || typeof worker === 'function')
      && typeof worker.postMessage === 'function'
      && typeof worker.terminate === 'function';
  }

  function terminate(worker) {
    try {
      if (worker && typeof worker.terminate === 'function') worker.terminate();
    } catch (error) {
      // A failed cleanup must not prevent raw-text fallback.
    }
  }

  function createMarkdownRenderCoordinator(options) {
    const config = options || {};
    const createWorker = typeof config.createWorker === 'function' ? config.createWorker : defaultCreateWorker;
    const schedule = typeof config.setTimeout === 'function' ? config.setTimeout : defaultSetTimeout;
    const cancelTimer = typeof config.clearTimeout === 'function' ? config.clearTimeout : defaultClearTimeout;
    const configuredWorkerUrl = typeof config.workerUrl === 'string' && config.workerUrl;
    const renderWorkerUrl = configuredWorkerUrl || workerUrl;
    const timeoutMs = validTimeout(config.timeoutMs) ? config.timeoutMs : defaultTimeoutMs;
    const activeJobs = new WeakMap();
    const versions = new WeakMap();
    let nextId = 1;

    function writeRaw(element, source) {
      try {
        element.textContent = source;
      } catch (error) {
        // The coordinator has no safer DOM operation available for an invalid element.
      }
    }

    function isCurrent(job) {
      return !job.cleaned
        && activeJobs.get(job.element) === job
        && versions.get(job.element) === job.version;
    }

    function cleanup(job) {
      if (job.cleaned) return;
      job.cleaned = true;

      if (activeJobs.get(job.element) === job) activeJobs.delete(job.element);
      try { job.worker.onmessage = null; } catch (error) { /* ignore cleanup errors */ }
      try { job.worker.onerror = null; } catch (error) { /* ignore cleanup errors */ }
      if (job.timerStarted) {
        try { cancelTimer(job.timer); } catch (error) { /* ignore cleanup errors */ }
      }
      terminate(job.worker);
    }

    function fallback(job) {
      if (!isCurrent(job)) return;
      cleanup(job);
      writeRaw(job.element, job.source);
    }

    function complete(job, html) {
      if (!isCurrent(job)) return;
      cleanup(job);
      try {
        job.element.innerHTML = html;
      } catch (error) {
        writeRaw(job.element, job.source);
      }
    }

    function renderInto(element, value) {
      if (!isElementKey(element)) return element;

      const source = text(value);
      const previous = activeJobs.get(element);
      if (previous) cleanup(previous);

      const version = (versions.get(element) || 0) + 1;
      versions.set(element, version);

      let worker;
      try {
        worker = createWorker(renderWorkerUrl);
      } catch (error) {
        writeRaw(element, source);
        return element;
      }

      if (!usableWorker(worker)) {
        terminate(worker);
        writeRaw(element, source);
        return element;
      }

      const job = {
        element,
        worker,
        id: nextId++,
        version,
        source,
        timer: undefined,
        timerStarted: false,
        cleaned: false,
      };
      activeJobs.set(element, job);

      try {
        worker.onmessage = event => {
          if (!isCurrent(job)) return;
          try {
            const message = event && event.data;
            if (!message || message.id !== job.id || typeof message.html !== 'string') {
              fallback(job);
              return;
            }
            complete(job, message.html);
          } catch (error) {
            fallback(job);
          }
        };
        worker.onerror = () => fallback(job);

        const timer = schedule(() => fallback(job), timeoutMs);
        if (!isCurrent(job)) {
          try { cancelTimer(timer); } catch (error) { /* ignore cleanup errors */ }
          return element;
        }
        job.timer = timer;
        job.timerStarted = true;
        worker.postMessage({ id: job.id, text: source });
      } catch (error) {
        fallback(job);
      }

      return element;
    }

    return { renderInto };
  }

  return { createMarkdownRenderCoordinator };
}));
