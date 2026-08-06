import { parseMarkdown } from './ast';
import type {
  MarkdownWorkerRequest,
  MarkdownWorkerResponse,
} from './workerClient';

interface MarkdownWorkerScope {
  onmessage: ((event: MessageEvent<unknown>) => void) | null;
  postMessage(message: MarkdownWorkerResponse): void;
}

function decodeRequest(value: unknown): MarkdownWorkerRequest | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null;

  const id = Reflect.get(value, 'id') as unknown;
  const generation = Reflect.get(value, 'generation') as unknown;
  const source = Reflect.get(value, 'source') as unknown;
  if (!Number.isSafeInteger(id) || (id as number) < 1) return null;
  if (!Number.isSafeInteger(generation) || (generation as number) < 0) return null;
  if (typeof source !== 'string') return null;

  return { id: id as number, generation: generation as number, source };
}

export function processMarkdownWorkerRequest(
  value: unknown,
): MarkdownWorkerResponse | null {
  const request = decodeRequest(value);
  if (request === null) return null;

  try {
    return {
      id: request.id,
      generation: request.generation,
      document: parseMarkdown(request.source),
    };
  } catch {
    return {
      id: request.id,
      generation: request.generation,
      error: true,
    };
  }
}

const scope = globalThis as unknown as MarkdownWorkerScope;
if (typeof document === 'undefined' && typeof scope.postMessage === 'function') {
  scope.onmessage = (event) => {
    const response = processMarkdownWorkerRequest(event.data);
    if (response !== null) scope.postMessage(response);
  };
}
