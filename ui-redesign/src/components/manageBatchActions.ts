/**
 * Small, UI-independent seams for management mutations.
 *
 * The legacy API can answer HTTP 200 with `{ ok: false }`.  Callers that
 * perform a mutation must therefore validate the business result instead of
 * treating a resolved promise as success.  Batch streams can also have
 * committed some items before their terminal failure; recovery refreshes are
 * best-effort and must run independently so one stale read cannot hide the
 * other.
 */

export function assertMutationOk(
  result: unknown,
  fallback = '操作失败',
): asserts result is { ok: true; error?: string } {
  if (
    result !== null &&
    typeof result === 'object' &&
    (result as { ok?: unknown }).ok === true
  ) {
    return;
  }

  const detail =
    result !== null && typeof result === 'object'
      ? (result as { error?: unknown }).error
      : undefined;
  const message = typeof detail === 'string' && detail.trim() ? detail : fallback;
  throw new Error(message);
}

export async function recoverBatchFailure(
  reloadPapers: () => Promise<unknown>,
  refreshStatus?: () => Promise<unknown> | unknown,
): Promise<void> {
  const tasks: Array<Promise<unknown>> = [Promise.resolve().then(reloadPapers)];
  if (refreshStatus) {
    tasks.push(Promise.resolve().then(refreshStatus));
  }
  await Promise.allSettled(tasks);
}
