import {
  SelectionTranslator,
  type SelectionTranslationCommit,
} from './SelectionTranslator';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

it('aborts the previous request and rejects its late result before commit', async () => {
  const first = deferred<string>();
  const second = deferred<string>();
  const signals: AbortSignal[] = [];
  const translate = vi
    .fn((_: string, signal: AbortSignal) => {
      signals.push(signal);
      return signals.length === 1 ? first.promise : second.promise;
    });
  const commits: SelectionTranslationCommit<string>[] = [];
  const translator = new SelectionTranslator<string>({ translate });
  translator.updateContext({ paperId: 'paper-a', generation: 4 });

  const firstRequest = translator.translate(
    { text: 'first selection', anchor: 'first-anchor' },
    (commit) => commits.push(commit),
  );
  const secondRequest = translator.translate(
    { text: 'second selection', anchor: 'second-anchor' },
    (commit) => commits.push(commit),
  );

  expect(signals[0]?.aborted).toBe(true);
  second.resolve('第二段');
  await expect(secondRequest).resolves.toMatchObject({
    requestId: 2,
    paperId: 'paper-a',
    generation: 4,
    sourceText: 'second selection',
    translatedText: '第二段',
    anchor: 'second-anchor',
  });

  first.resolve('迟到结果');
  await expect(firstRequest).resolves.toBeNull();
  expect(commits).toHaveLength(1);
  expect(commits[0]?.requestId).toBe(2);
});

it('aborts immediately when paper generation changes and drops the late result', async () => {
  const pending = deferred<string>();
  let signal: AbortSignal | undefined;
  const commits: SelectionTranslationCommit<null>[] = [];
  const translator = new SelectionTranslator<null>({
    translate: vi.fn((_, requestSignal) => {
      signal = requestSignal;
      return pending.promise;
    }),
  });
  translator.updateContext({ paperId: 'paper-a', generation: 1 });
  const request = translator.translate(
    { text: 'selected text', anchor: null },
    (commit) => commits.push(commit),
  );

  translator.updateContext({ paperId: 'paper-b', generation: 2 });
  expect(signal?.aborted).toBe(true);
  pending.resolve('迟到翻译');

  await expect(request).resolves.toBeNull();
  expect(commits).toEqual([]);
});

it('rejects empty and oversized input locally without truncating or requesting', async () => {
  const translate = vi.fn(async () => 'unused');
  const translator = new SelectionTranslator<null>({ translate });
  translator.updateContext({ paperId: 'paper-a', generation: 1 });

  await expect(
    translator.translate({ text: '   ', anchor: null }),
  ).rejects.toMatchObject({
    code: 'empty',
    length: 0,
  });
  await expect(
    translator.translate({ text: 'x'.repeat(6_001), anchor: null }),
  ).rejects.toMatchObject({
    code: 'too-long',
    length: 6_001,
  });
  expect(translate).not.toHaveBeenCalled();
});

it('is idempotently disposable and reusable by a StrictMode probe', async () => {
  const translate = vi.fn(async (text: string) => `译：${text}`);
  const translator = new SelectionTranslator<null>({ translate });
  translator.updateContext({ paperId: 'paper-a', generation: 1 });
  translator.dispose();
  translator.dispose();

  translator.updateContext({ paperId: 'paper-a', generation: 2 });
  await expect(
    translator.translate({ text: 'new owner', anchor: null }),
  ).resolves.toMatchObject({ requestId: 1, generation: 2 });
});
