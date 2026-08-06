import {
  decodeSafeDocument,
  plainTextDocument,
  type SafeDocument,
} from './ast';

export interface MarkdownWorkerRequest {
  id: number;
  generation: number;
  source: string;
}

export type MarkdownWorkerResponse =
  | { id: number; generation: number; document: SafeDocument }
  | { id: number; generation: number; error: true };

export interface MarkdownWorkerLike {
  onmessage: ((event: MessageEvent<unknown>) => void) | null;
  onerror: ((event: Event) => void) | null;
  onmessageerror: ((event: MessageEvent<unknown>) => void) | null;
  postMessage(message: unknown): void;
  terminate(): void;
}

export interface MarkdownRenderOptions {
  generation: number;
  signal?: AbortSignal;
}

export interface MarkdownWorkerClient {
  render(source: string, options: MarkdownRenderOptions): Promise<SafeDocument>;
  cancel(): void;
  dispose(): void;
}

export interface MarkdownWorkerClientOptions {
  workerFactory?: () => MarkdownWorkerLike;
  timeoutMs?: number;
}

interface ActiveRequest {
  id: number;
  generation: number;
  source: string;
  worker: MarkdownWorkerLike;
  signal?: AbortSignal;
  abortHandler?: () => void;
  timer?: ReturnType<typeof setTimeout>;
  settled: boolean;
  finish(document: SafeDocument): void;
}

function defaultWorkerFactory(): MarkdownWorkerLike {
  return new Worker(
    new URL('./markdown.worker.ts', import.meta.url),
    { type: 'module', name: 'paper-study-markdown' },
  ) as unknown as MarkdownWorkerLike;
}

function boundedTimeout(value: number | undefined): number {
  if (!Number.isFinite(value)) return 4_000;
  return Math.max(1, Math.min(60_000, Math.trunc(value ?? 4_000)));
}

function messageIdentity(value: unknown): { id: number; generation: number } | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;
  const id = Reflect.get(value, 'id') as unknown;
  const generation = Reflect.get(value, 'generation') as unknown;
  return Number.isInteger(id) && Number.isInteger(generation)
    ? { id: id as number, generation: generation as number }
    : null;
}

export function createMarkdownWorkerClient(
  options: MarkdownWorkerClientOptions = {},
): MarkdownWorkerClient {
  const workerFactory = options.workerFactory ?? defaultWorkerFactory;
  const timeoutMs = boundedTimeout(options.timeoutMs);
  let nextId = 1;
  let active: ActiveRequest | null = null;

  const cancelActive = () => {
    const request = active;
    if (request) request.finish(plainTextDocument(request.source));
  };

  const render = (
    sourceValue: string,
    renderOptions: MarkdownRenderOptions,
  ): Promise<SafeDocument> => {
    const source = String(sourceValue);
    cancelActive();

    const generation = renderOptions.generation;
    if (!Number.isInteger(generation) || renderOptions.signal?.aborted) {
      return Promise.resolve(plainTextDocument(source));
    }

    let worker: MarkdownWorkerLike;
    try {
      worker = workerFactory();
    } catch {
      return Promise.resolve(plainTextDocument(source));
    }

    return new Promise<SafeDocument>((resolve) => {
      const request: ActiveRequest = {
        id: nextId++,
        generation,
        source,
        worker,
        signal: renderOptions.signal,
        settled: false,
        finish: () => undefined,
      };

      request.finish = (document) => {
        if (request.settled) return;
        request.settled = true;
        if (active === request) active = null;
        if (request.timer !== undefined) clearTimeout(request.timer);
        if (request.signal && request.abortHandler) {
          request.signal.removeEventListener('abort', request.abortHandler);
        }
        request.worker.onmessage = null;
        request.worker.onerror = null;
        request.worker.onmessageerror = null;
        try {
          request.worker.terminate();
        } finally {
          resolve(document);
        }
      };

      request.worker.onmessage = (event) => {
        if (request.settled || active !== request) return;
        const identity = messageIdentity(event.data);
        if (identity === null) {
          request.finish(plainTextDocument(source));
          return;
        }
        if (identity.id !== request.id || identity.generation !== request.generation) return;
        if (typeof event.data !== 'object' || event.data === null) {
          request.finish(plainTextDocument(source));
          return;
        }
        if (Reflect.get(event.data, 'error') === true) {
          request.finish(plainTextDocument(source));
          return;
        }
        try {
          request.finish(decodeSafeDocument(Reflect.get(event.data, 'document')));
        } catch {
          request.finish(plainTextDocument(source));
        }
      };
      request.worker.onerror = () => request.finish(plainTextDocument(source));
      request.worker.onmessageerror = () => request.finish(plainTextDocument(source));
      request.abortHandler = () => request.finish(plainTextDocument(source));
      request.signal?.addEventListener('abort', request.abortHandler, { once: true });
      request.timer = setTimeout(
        () => request.finish(plainTextDocument(source)),
        timeoutMs,
      );
      active = request;

      const message: MarkdownWorkerRequest = {
        id: request.id,
        generation: request.generation,
        source,
      };
      try {
        request.worker.postMessage(message);
      } catch {
        request.finish(plainTextDocument(source));
      }
    });
  };

  return {
    render,
    cancel: cancelActive,
    dispose: cancelActive,
  };
}
