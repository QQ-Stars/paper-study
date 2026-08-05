export type ApiErrorKind = 'http' | 'network' | 'business' | 'decode' | 'protocol';

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status?: number;
  readonly body?: unknown;
  readonly code?: string;
  readonly path?: string;

  constructor(
    kind: ApiErrorKind,
    message: string,
    options: {
      status?: number;
      body?: unknown;
      code?: string;
      path?: string;
      cause?: unknown;
    } = {},
  ) {
    super(message, options.cause === undefined ? undefined : { cause: options.cause });
    this.name = 'ApiError';
    this.kind = kind;
    this.status = options.status;
    this.body = options.body;
    this.code = options.code;
    this.path = options.path;
  }
}

export class HttpError extends ApiError {
  readonly statusText: string;

  constructor(status: number, statusText: string, body: unknown) {
    const suffix = typeof body === 'string' && body.trim() ? `: ${body.trim()}` : '';
    super('http', `HTTP ${status}${statusText ? ` ${statusText}` : ''}${suffix}`, { status, body });
    this.name = 'HttpError';
    this.statusText = statusText;
  }
}

export class NetworkError extends ApiError {
  constructor(cause: unknown) {
    const detail = cause instanceof Error ? cause.message : String(cause);
    super('network', `Network request failed: ${detail}`, { cause });
    this.name = 'NetworkError';
  }
}

export class BusinessError extends ApiError {
  constructor(message: string, body?: unknown, code?: string) {
    super('business', message, { body, code });
    this.name = 'BusinessError';
  }
}

export class DecodeError extends ApiError {
  constructor(path: string, expected: string, value?: unknown) {
    super('decode', `Invalid value at ${path}: expected ${expected}`, { path, body: value });
    this.name = 'DecodeError';
  }
}

export class ProtocolError extends ApiError {
  readonly line?: number;

  constructor(code: string, message: string, line?: number, cause?: unknown) {
    super('protocol', line === undefined ? message : `${message} at NDJSON line ${line}`, {
      code,
      path: line === undefined ? undefined : `$line[${line}]`,
      cause,
    });
    this.name = 'ProtocolError';
    this.line = line;
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException
    ? error.name === 'AbortError'
    : error instanceof Error && error.name === 'AbortError';
}
