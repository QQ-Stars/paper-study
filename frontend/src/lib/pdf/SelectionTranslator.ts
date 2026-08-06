import { workspaceApi } from '../api/workspaceApi';
import { isAbortError } from '../api/errors';

export interface SelectionTranslationContext {
  paperId: string;
  generation: number;
}

export interface SelectionTranslationInput<Anchor> {
  text: string;
  anchor: Anchor;
}

export interface SelectionTranslationCommit<Anchor> {
  requestId: number;
  paperId: string;
  generation: number;
  sourceText: string;
  translatedText: string;
  anchor: Anchor;
}

export interface SelectionTranslatorDependencies {
  translate(text: string, signal: AbortSignal): Promise<string>;
}

export class SelectionTranslationInputError extends Error {
  readonly code: 'empty' | 'too-long';
  readonly length: number;

  constructor(code: 'empty' | 'too-long', length: number) {
    super(
      code === 'empty'
        ? 'Selection text must not be empty'
        : 'Selection text exceeds 6000 characters',
    );
    this.name = 'SelectionTranslationInputError';
    this.code = code;
    this.length = length;
  }
}

interface ActiveTranslation {
  requestId: number;
  controller: AbortController;
}

const defaultDependencies: SelectionTranslatorDependencies = {
  translate: async (text, signal) => {
    const result = await workspaceApi.translateText(text, signal);
    return result.text;
  },
};

export class SelectionTranslator<Anchor> {
  readonly #dependencies: SelectionTranslatorDependencies;
  #context: SelectionTranslationContext | null = null;
  #requestId = 0;
  #active: ActiveTranslation | null = null;
  #disposed = false;

  constructor(
    dependencies: SelectionTranslatorDependencies = defaultDependencies,
  ) {
    this.#dependencies = dependencies;
  }

  updateContext(context: SelectionTranslationContext): void {
    const paperId = String(context.paperId).trim();
    if (!paperId) throw new TypeError('paperId must not be empty');
    if (!Number.isInteger(context.generation) || context.generation < 1) {
      throw new RangeError('generation must be a positive integer');
    }
    if (
      this.#context?.paperId === paperId &&
      this.#context.generation === context.generation
    ) {
      return;
    }

    this.#abortActive();
    this.#context = { paperId, generation: context.generation };
    this.#disposed = false;
  }

  async translate(
    input: SelectionTranslationInput<Anchor>,
    onCommit?: (commit: SelectionTranslationCommit<Anchor>) => void,
  ): Promise<SelectionTranslationCommit<Anchor> | null> {
    if (!this.#context || this.#disposed) {
      throw new Error('SelectionTranslator requires an active paper context');
    }
    const sourceText = input.text.trim();
    if (!sourceText) throw new SelectionTranslationInputError('empty', 0);
    if (sourceText.length > 6_000) {
      throw new SelectionTranslationInputError('too-long', sourceText.length);
    }

    this.#abortActive();
    const context = { ...this.#context };
    const requestId = ++this.#requestId;
    const controller = new AbortController();
    const active = { requestId, controller };
    this.#active = active;

    try {
      const translatedText = await this.#dependencies.translate(
        sourceText,
        controller.signal,
      );
      if (!this.#isCurrent(active, context)) return null;
      this.#active = null;
      const commit: SelectionTranslationCommit<Anchor> = {
        requestId,
        paperId: context.paperId,
        generation: context.generation,
        sourceText,
        translatedText,
        anchor: input.anchor,
      };
      onCommit?.(commit);
      return commit;
    } catch (error) {
      if (!this.#isCurrent(active, context) || isAbortError(error)) return null;
      this.#active = null;
      throw error;
    }
  }

  abort(): void {
    this.#abortActive();
  }

  dispose(): void {
    if (this.#disposed) return;
    this.#disposed = true;
    this.#abortActive();
    this.#context = null;
  }

  #abortActive(): void {
    this.#active?.controller.abort();
    this.#active = null;
  }

  #isCurrent(
    active: ActiveTranslation,
    context: SelectionTranslationContext,
  ): boolean {
    return (
      !this.#disposed &&
      this.#active === active &&
      !active.controller.signal.aborted &&
      this.#context?.paperId === context.paperId &&
      this.#context.generation === context.generation
    );
  }
}
