import { afterEach, describe, expect, it, vi } from 'vitest';

import { plainTextDocument, type SafeDocument } from './ast';
import {
  createMarkdownWorkerClient,
  type MarkdownWorkerLike,
  type MarkdownWorkerRequest,
} from './workerClient';

class ControlledWorker implements MarkdownWorkerLike {
  static live = 0;

  onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessageerror: ((event: MessageEvent<unknown>) => void) | null = null;
  readonly sent: unknown[] = [];
  terminated = false;

  constructor() {
    ControlledWorker.live += 1;
  }

  postMessage(message: unknown): void {
    this.sent.push(message);
  }

  terminate(): void {
    if (this.terminated) return;
    this.terminated = true;
    ControlledWorker.live -= 1;
  }

  emitMessage(data: unknown): void {
    this.onmessage?.({ data } as MessageEvent<unknown>);
  }

  emitError(): void {
    this.onerror?.(new Event('error'));
  }

  emitMessageError(): void {
    this.onmessageerror?.({ data: null } as MessageEvent<unknown>);
  }
}

const documentFor = (value: string): SafeDocument => ({
  version: 1,
  nodes: [{ type: 'paragraph', children: [{ type: 'text', value }] }],
});

const parsedResultFor = (value: string) => ({
  status: 'parsed' as const,
  document: documentFor(value),
});

const fallbackResultFor = (source: string) => ({
  status: 'fallback' as const,
  document: plainTextDocument(source),
});

afterEach(() => {
  vi.useRealTimers();
  ControlledWorker.live = 0;
});

describe('createMarkdownWorkerClient', () => {
  it('accepts a matching structured-clone document and releases the request owner', async () => {
    const workers: ControlledWorker[] = [];
    const client = createMarkdownWorkerClient({
      workerFactory: () => {
        const worker = new ControlledWorker();
        workers.push(worker);
        return worker;
      },
    });

    const pending = client.render('source', { generation: 7 });
    const worker = workers[0]!;
    const request = worker.sent[0] as MarkdownWorkerRequest;
    worker.emitMessage({
      id: request.id,
      generation: request.generation,
      document: documentFor('rendered'),
    });

    await expect(pending).resolves.toEqual(parsedResultFor('rendered'));
    expect(worker.terminated).toBe(true);
    expect(worker.onmessage).toBeNull();
    expect(worker.onerror).toBeNull();
    expect(worker.onmessageerror).toBeNull();
    expect(ControlledWorker.live).toBe(0);
  });

  it.each(['error', 'messageerror'] as const)(
    'falls back to inert text after a Worker %s',
    async (failure) => {
      const worker = new ControlledWorker();
      const client = createMarkdownWorkerClient({ workerFactory: () => worker });
      const pending = client.render('<script>alert(1)</script>', { generation: 1 });

      if (failure === 'error') worker.emitError();
      else worker.emitMessageError();

      await expect(pending).resolves.toEqual(fallbackResultFor('<script>alert(1)</script>'));
      expect(worker.terminated).toBe(true);
      expect(ControlledWorker.live).toBe(0);
    },
  );

  it('times out to inert text and terminates the Worker', async () => {
    vi.useFakeTimers();
    const worker = new ControlledWorker();
    const client = createMarkdownWorkerClient({ workerFactory: () => worker, timeoutMs: 25 });
    const pending = client.render('*pathological*', { generation: 2 });

    await vi.advanceTimersByTimeAsync(25);

    await expect(pending).resolves.toEqual(fallbackResultFor('*pathological*'));
    expect(worker.terminated).toBe(true);
    expect(ControlledWorker.live).toBe(0);
  });

  it('supersedes an old generation and ignores its saved late message handler', async () => {
    const workers: ControlledWorker[] = [];
    const client = createMarkdownWorkerClient({
      workerFactory: () => {
        const worker = new ControlledWorker();
        workers.push(worker);
        return worker;
      },
    });

    const oldPending = client.render('old source', { generation: 3 });
    const oldWorker = workers[0]!;
    const oldRequest = oldWorker.sent[0] as MarkdownWorkerRequest;
    const savedOldHandler = oldWorker.onmessage!;

    const newPending = client.render('new source', { generation: 4 });
    const newWorker = workers[1]!;
    const newRequest = newWorker.sent[0] as MarkdownWorkerRequest;

    savedOldHandler({
      data: {
        id: oldRequest.id,
        generation: oldRequest.generation,
        document: documentFor('late old result'),
      },
    } as MessageEvent<unknown>);
    newWorker.emitMessage({
      id: newRequest.id,
      generation: newRequest.generation,
      document: documentFor('current result'),
    });

    await expect(oldPending).resolves.toEqual(fallbackResultFor('old source'));
    await expect(newPending).resolves.toEqual(parsedResultFor('current result'));
    expect(oldWorker.terminated).toBe(true);
    expect(newWorker.terminated).toBe(true);
    expect(ControlledWorker.live).toBe(0);
  });

  it('settles with inert text on dispose and cannot be changed by a late message', async () => {
    const worker = new ControlledWorker();
    const client = createMarkdownWorkerClient({ workerFactory: () => worker });
    const pending = client.render('unmounted source', { generation: 5 });
    const request = worker.sent[0] as MarkdownWorkerRequest;
    const savedHandler = worker.onmessage!;

    client.dispose();
    savedHandler({
      data: {
        id: request.id,
        generation: request.generation,
        document: documentFor('late result'),
      },
    } as MessageEvent<unknown>);

    await expect(pending).resolves.toEqual(fallbackResultFor('unmounted source'));
    expect(worker.terminated).toBe(true);
    expect(ControlledWorker.live).toBe(0);
  });
});
