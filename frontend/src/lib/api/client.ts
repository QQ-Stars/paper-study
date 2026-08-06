import {
  ApiError,
  DecodeError,
  HttpError,
  NetworkError,
  ProtocolError,
  isAbortError,
} from './errors';
import type { Decoder } from './types';
import type { StreamContract } from '../streaming/contracts';
import { readNdjsonResponse } from '../streaming/ndjson';
import type { StreamObserver } from '../streaming/ndjson';

export type FetchLike = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

export interface TransportOptions<E = never, T = never> extends RequestInit {
  fetchImpl?: FetchLike;
  onEvent?: StreamObserver<E, T>;
}

export interface ApiClient {
  json<T>(input: RequestInfo | URL, decoder: Decoder<T>, options?: TransportOptions<unknown, unknown>): Promise<T>;
  text(input: RequestInfo | URL, options?: TransportOptions<unknown, unknown>): Promise<string>;
  bytes(input: RequestInfo | URL, options?: TransportOptions<unknown, unknown>): Promise<ArrayBuffer>;
  ndjson<E, T>(
    input: RequestInfo | URL,
    contract: StreamContract<E, T>,
    options?: TransportOptions<E, T>,
  ): Promise<T>;
}

export function jsonRequest(body: unknown, init: RequestInit = {}): RequestInit {
  const headers = new Headers(init.headers);
  headers.set('Content-Type', 'application/json');
  return { ...init, headers, body: JSON.stringify(body) };
}

function bodyDetail(text: string): unknown {
  if (!text.trim()) return '';
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function requestMethod(
  input: RequestInfo | URL,
  init: RequestInit,
): string {
  const fromRequest = typeof Request !== 'undefined' && input instanceof Request
    ? input.method
    : undefined;
  return (init.method ?? fromRequest ?? 'GET').toUpperCase();
}

async function consumeBody<T>(
  read: () => Promise<T>,
  method: string,
): Promise<T> {
  try {
    return await read();
  } catch (error) {
    if (isAbortError(error)) throw error;
    throw new NetworkError(error, method);
  }
}

export function createApiClient(defaultFetch?: FetchLike): ApiClient {
  const request = async <E, T>(
    input: RequestInfo | URL,
    options: TransportOptions<E, T>,
  ): Promise<Response> => {
    const { fetchImpl, onEvent, ...init } = options;
    void onEvent;
    const selectedFetch = fetchImpl ?? defaultFetch ?? globalThis.fetch;
    const method = requestMethod(input, init);
    try {
      return await selectedFetch(input, init);
    } catch (error) {
      if (isAbortError(error)) throw error;
      throw new NetworkError(error, method);
    }
  };

  const responseFor = async <E, T>(
    input: RequestInfo | URL,
    options: TransportOptions<E, T>,
  ): Promise<Response> => {
    const method = requestMethod(input, options);
    const response = await request(input, options);
    if (response.ok) return response;
    const text = await consumeBody(() => response.text(), method);
    throw new HttpError(
      response.status,
      response.statusText,
      bodyDetail(text),
      method,
    );
  };

  return {
    async json<T>(
      input: RequestInfo | URL,
      decoder: Decoder<T>,
      options: TransportOptions<unknown, unknown> = {},
    ): Promise<T> {
      const response = await responseFor(input, options);
      const text = await consumeBody(
        () => response.text(),
        requestMethod(input, options),
      );
      let value: unknown;
      try {
        value = JSON.parse(text);
      } catch (error) {
        throw new ProtocolError('invalid-json', 'Response body is not valid JSON', undefined, error);
      }
      try {
        return decoder(value, '$');
      } catch (error) {
        if (error instanceof DecodeError) throw error;
        throw error;
      }
    },

    async text(input, options = {}) {
      const response = await responseFor(input, options);
      return consumeBody(
        () => response.text(),
        requestMethod(input, options),
      );
    },

    async bytes(input, options = {}) {
      const response = await responseFor(input, options);
      return consumeBody(
        () => response.arrayBuffer(),
        requestMethod(input, options),
      );
    },

    async ndjson<E, T>(input: RequestInfo | URL, contract: StreamContract<E, T>, options: TransportOptions<E, T> = {}) {
      const response = await responseFor(input, options);
      try {
        return await readNdjsonResponse(response, contract, options.onEvent);
      } catch (error) {
        if (isAbortError(error) || error instanceof ApiError) throw error;
        throw new NetworkError(error, requestMethod(input, options));
      }
    },
  };
}

export const api = createApiClient();
