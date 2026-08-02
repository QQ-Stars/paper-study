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

  function workerCapabilities(worker) {
    try {
      if (!worker || (typeof worker !== 'object' && typeof worker !== 'function')) return null;
      const postMessage = worker.postMessage;
      const terminateWorker = worker.terminate;
      if (typeof postMessage !== 'function' || typeof terminateWorker !== 'function') return null;
      return { postMessage, terminate: terminateWorker };
    } catch (error) {
      return null;
    }
  }

  function terminate(worker, knownTerminate) {
    try {
      const terminateWorker = typeof knownTerminate === 'function'
        ? knownTerminate
        : worker && worker.terminate;
      if (typeof terminateWorker === 'function') terminateWorker.call(worker);
    } catch (error) {
      // A failed cleanup must not prevent a still-current raw-text fallback.
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

    function ownsVersion(job) {
      return versions.get(job.element) === job.version;
    }

    function isCurrent(job) {
      return !job.cleaned && ownsVersion(job) && activeJobs.get(job.element) === job;
    }

    function clear(timer) {
      try { cancelTimer(timer); } catch (error) { /* ignore cleanup errors */ }
    }

    function cleanup(job) {
      if (!job || job.cleaned) return;
      job.cleaned = true;

      if (activeJobs.get(job.element) === job) activeJobs.delete(job.element);
      if (job.worker) {
        try { job.worker.onmessage = null; } catch (error) { /* ignore cleanup errors */ }
        try { job.worker.onerror = null; } catch (error) { /* ignore cleanup errors */ }
        try { job.worker.onmessageerror = null; } catch (error) { /* ignore cleanup errors */ }
      }
      if (job.timerStarted) {
        clear(job.timer);
      }
      terminate(job.worker, job.terminate);
    }

    function cancel(element) {
      if (!isElementKey(element)) return element;

      const previous = activeJobs.get(element);
      const version = (versions.get(element) || 0) + 1;
      const reservation = {
        element,
        version,
        source: '',
        cleaned: false,
      };
      versions.set(element, version);
      activeJobs.set(element, reservation);

      if (previous) cleanup(previous);
      if (activeJobs.get(element) === reservation) activeJobs.delete(element);
      reservation.cleaned = true;
      return element;
    }

    function fallback(job) {
      if (!isCurrent(job)) return;
      cleanup(job);
      if (ownsVersion(job)) writeRaw(job.element, job.source);
    }

    function complete(job, html) {
      if (!isCurrent(job)) return;
      cleanup(job);
      if (!ownsVersion(job)) return;
      try {
        job.element.innerHTML = html;
      } catch (error) {
        if (ownsVersion(job)) writeRaw(job.element, job.source);
      }
    }

    function renderInto(element, value) {
      if (!isElementKey(element)) return element;

      const previous = activeJobs.get(element);
      const version = (versions.get(element) || 0) + 1;
      const reservation = {
        element,
        version,
        source: '',
        cleaned: false,
      };
      versions.set(element, version);
      activeJobs.set(element, reservation);

      if (previous) cleanup(previous);
      if (!isCurrent(reservation)) return element;

      reservation.source = text(value);
      if (!isCurrent(reservation)) return element;

      let worker;
      try {
        worker = createWorker(renderWorkerUrl);
      } catch (error) {
        fallback(reservation);
        return element;
      }

      if (!isCurrent(reservation)) {
        terminate(worker);
        return element;
      }

      const capabilities = workerCapabilities(worker);
      if (!isCurrent(reservation)) {
        terminate(worker, capabilities && capabilities.terminate);
        return element;
      }
      if (!capabilities) {
        terminate(worker);
        fallback(reservation);
        return element;
      }

      const job = {
        element,
        worker,
        id: nextId++,
        version,
        source: reservation.source,
        timer: undefined,
        timerStarted: false,
        cleaned: false,
        postMessage: capabilities.postMessage,
        terminate: capabilities.terminate,
      };
      activeJobs.set(element, job);
      reservation.cleaned = true;

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
        if (!isCurrent(job)) {
          cleanup(job);
          return element;
        }
        worker.onerror = () => fallback(job);
        if (!isCurrent(job)) {
          cleanup(job);
          return element;
        }
        worker.onmessageerror = () => fallback(job);
        if (!isCurrent(job)) {
          cleanup(job);
          return element;
        }

        const timer = schedule(() => fallback(job), timeoutMs);
        if (!isCurrent(job)) {
          clear(timer);
          cleanup(job);
          return element;
        }
        job.timer = timer;
        job.timerStarted = true;
        job.postMessage.call(worker, { id: job.id, text: job.source });
        if (!isCurrent(job)) {
          cleanup(job);
          return element;
        }
      } catch (error) {
        fallback(job);
      }

      return element;
    }

    return { renderInto, cancel };
  }

  return { createMarkdownRenderCoordinator };
}));
