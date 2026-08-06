import { QueryClient } from '@tanstack/react-query';

import { ApiError, isAbortError } from '../../lib/api/errors';

interface RequestErrorShape {
  kind?: unknown;
  name?: unknown;
  requestMethod?: unknown;
  status?: unknown;
}

function readErrorShape(error: unknown): RequestErrorShape {
  return error && typeof error === 'object'
    ? (error as RequestErrorShape)
    : {};
}

export function shouldRetryWorkspaceQuery(
  failureCount: number,
  error: unknown,
): boolean {
  const shape = readErrorShape(error);
  if (
    failureCount >= 1 ||
    isAbortError(error) ||
    shape.name === 'AbortError'
  ) {
    return false;
  }

  if (
    typeof shape.requestMethod === 'string' &&
    shape.requestMethod.toUpperCase() !== 'GET'
  ) {
    return false;
  }

  if (typeof shape.status === 'number') {
    return shape.status >= 500 && shape.status < 600;
  }

  if (error instanceof ApiError) {
    return error.kind === 'network';
  }

  return shape.kind === 'network';
}

export function createWorkspaceQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetryWorkspaceQuery,
        refetchOnWindowFocus: false,
      },
      mutations: {
        retry: false,
      },
    },
  });
}
