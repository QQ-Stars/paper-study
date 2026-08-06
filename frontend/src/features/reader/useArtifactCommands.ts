import { useCallback, useEffect, useReducer, useRef } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { isAbortError } from '../../lib/api/errors';
import { paperApi } from '../../lib/api/paperApi';
import { workspaceApi } from '../../lib/api/workspaceApi';
import {
  artifactReconciliationKeys,
  commandForIdentity,
  emptyArtifactSession,
  reduceArtifactSession,
  type ArtifactCommandOwner,
  type ArtifactKind,
} from './artifactSession';

interface ActiveCommand {
  owner: ArtifactCommandOwner;
  controller: AbortController;
}

type ActiveCommands = Partial<Record<ArtifactKind, ActiveCommand>>;
export type ArtifactCommandOutcome = 'success' | 'failure' | 'stopped' | 'stale';
type ArtifactOperation = (
  signal: AbortSignal,
  reportProgress: (line: string) => void,
) => Promise<unknown>;

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message.trim()
    : '未知错误';
}

export function useArtifactCommands(paperId: string, generation: number) {
  const queryClient = useQueryClient();
  const [session, dispatch] = useReducer(
    reduceArtifactSession,
    undefined,
    emptyArtifactSession,
  );
  const nextRunId = useRef(1);
  const activeCommands = useRef<ActiveCommands>({});

  useEffect(() => () => {
    for (const kind of ['note', 'explainer', 'translation'] as const) {
      const active = activeCommands.current[kind];
      if (
        active?.owner.paperId === paperId
        && active.owner.generation === generation
      ) {
        delete activeCommands.current[kind];
        active.controller.abort();
      }
    }
  }, [generation, paperId]);

  const reconcile = useCallback(async (kind: ArtifactKind, fixedPaperId: string) => {
    const keys = artifactReconciliationKeys(kind, fixedPaperId);
    await Promise.allSettled(keys.map((queryKey) => (
      queryClient.invalidateQueries({ queryKey, exact: true })
    )));
  }, [queryClient]);

  const run = useCallback(async (
    kind: ArtifactKind,
    operation: ArtifactOperation,
  ): Promise<ArtifactCommandOutcome> => {
    const previous = activeCommands.current[kind];
    if (previous) {
      delete activeCommands.current[kind];
      previous.controller.abort();
      dispatch({ type: 'stopped', kind, owner: previous.owner });
    }

    const owner: ArtifactCommandOwner = {
      paperId,
      generation,
      runId: nextRunId.current,
    };
    nextRunId.current += 1;
    const active: ActiveCommand = { owner, controller: new AbortController() };
    activeCommands.current[kind] = active;
    const ownsRun = () => activeCommands.current[kind] === active;
    dispatch({ type: 'start', kind, owner });

    let outcome: ArtifactCommandOutcome;
    try {
      await operation(active.controller.signal, (line) => {
        if (ownsRun()) dispatch({ type: 'progress', kind, owner, line });
      });
      if (!ownsRun()) return 'stale';
      dispatch({ type: 'success', kind, owner });
      outcome = 'success';
    } catch (error) {
      if (!ownsRun()) return 'stale';
      if (isAbortError(error)) {
        dispatch({ type: 'stopped', kind, owner });
        outcome = 'stopped';
      } else {
        dispatch({ type: 'failure', kind, owner, error: errorMessage(error) });
        outcome = 'failure';
      }
    } finally {
      if (ownsRun()) delete activeCommands.current[kind];
      await reconcile(kind, owner.paperId);
    }
    return outcome;
  }, [generation, paperId, reconcile]);

  const saveNote = useCallback((content: string) => run(
    'note',
    (signal) => paperApi.saveNote(paperId, content, signal),
  ), [paperId, run]);

  const generateExplainer = useCallback((deep = false) => run(
    'explainer',
    (signal, reportProgress) => workspaceApi.explainPaper(paperId, deep, {
      signal,
      onEvent(event) {
        if (event.type === 'progress') reportProgress(event.line);
      },
    }),
  ), [paperId, run]);

  const generateTranslation = useCallback(() => run(
    'translation',
    (signal, reportProgress) => workspaceApi.translatePaper(paperId, {
      signal,
      onEvent(event) {
        if (event.type === 'progress') reportProgress(event.line);
      },
    }),
  ), [paperId, run]);

  const stop = useCallback((kind: ArtifactKind) => {
    const active = activeCommands.current[kind];
    if (!active) return;
    delete activeCommands.current[kind];
    active.controller.abort();
    dispatch({ type: 'stopped', kind, owner: active.owner });
  }, []);

  return {
    commands: {
      note: commandForIdentity(session.note, paperId, generation),
      explainer: commandForIdentity(session.explainer, paperId, generation),
      translation: commandForIdentity(session.translation, paperId, generation),
    },
    generateExplainer,
    generateTranslation,
    saveNote,
    stop,
  };
}
