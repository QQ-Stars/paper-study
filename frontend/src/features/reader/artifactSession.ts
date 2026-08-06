import { artifactKeys, paperKeys } from '../../lib/api/keys';
import { streamSideEffectPolicies } from '../../lib/api/workspaceApi';

export type ArtifactKind = 'note' | 'explainer' | 'translation';

export interface ArtifactCommandOwner {
  paperId: string;
  generation: number;
  runId: number;
}

export type ArtifactCommandState =
  | { status: 'idle' }
  | { status: 'running'; owner: ArtifactCommandOwner; progress: string[] }
  | { status: 'success'; owner: ArtifactCommandOwner }
  | { status: 'failure'; owner: ArtifactCommandOwner; error: string }
  | { status: 'stopped'; owner: ArtifactCommandOwner };

export type ArtifactSessionState = Record<ArtifactKind, ArtifactCommandState>;

export type ArtifactSessionAction =
  | { type: 'start'; kind: ArtifactKind; owner: ArtifactCommandOwner }
  | { type: 'progress'; kind: ArtifactKind; owner: ArtifactCommandOwner; line: string }
  | { type: 'success'; kind: ArtifactKind; owner: ArtifactCommandOwner }
  | { type: 'failure'; kind: ArtifactKind; owner: ArtifactCommandOwner; error: string }
  | { type: 'stopped'; kind: ArtifactKind; owner: ArtifactCommandOwner };

const idleCommand: ArtifactCommandState = { status: 'idle' };
const maximumProgressLines = 24;
const legacyEmptyExplainer = '*(暂无讲解)*';

export function emptyArtifactSession(): ArtifactSessionState {
  return {
    note: idleCommand,
    explainer: idleCommand,
    translation: idleCommand,
  };
}

function sameOwner(left: ArtifactCommandOwner, right: ArtifactCommandOwner): boolean {
  return left.paperId === right.paperId
    && left.generation === right.generation
    && left.runId === right.runId;
}

export function reduceArtifactSession(
  state: ArtifactSessionState,
  action: ArtifactSessionAction,
): ArtifactSessionState {
  if (action.type === 'start') {
    return {
      ...state,
      [action.kind]: { status: 'running', owner: action.owner, progress: [] },
    };
  }

  const current = state[action.kind];
  if (current.status !== 'running' || !sameOwner(current.owner, action.owner)) {
    return state;
  }

  if (action.type === 'progress') {
    const line = action.line.trim();
    if (!line) return state;
    return {
      ...state,
      [action.kind]: {
        ...current,
        progress: [...current.progress, line].slice(-maximumProgressLines),
      },
    };
  }
  if (action.type === 'success') {
    return { ...state, [action.kind]: { status: 'success', owner: action.owner } };
  }
  if (action.type === 'failure') {
    return {
      ...state,
      [action.kind]: { status: 'failure', owner: action.owner, error: action.error },
    };
  }
  return { ...state, [action.kind]: { status: 'stopped', owner: action.owner } };
}

export function commandForIdentity(
  command: ArtifactCommandState,
  paperId: string,
  generation: number,
): ArtifactCommandState {
  if (command.status === 'idle') return command;
  return command.owner.paperId === paperId && command.owner.generation === generation
    ? command
    : idleCommand;
}

export function normalizeArtifactText(value: string | null | undefined): string | null {
  if (value === null || value === undefined) return null;
  const trimmed = value.trim();
  return !trimmed || trimmed === legacyEmptyExplainer ? null : value;
}

function assertSettledStreamPolicy(kind: Exclude<ArtifactKind, 'note'>): void {
  const policy = kind === 'explainer'
    ? streamSideEffectPolicies.explain
    : streamSideEffectPolicies.translate;
  if (policy.reconcileOn !== 'settled') {
    throw new Error(`${kind} must reconcile its server facts on settle`);
  }
}

export function artifactReconciliationKeys(
  kind: ArtifactKind,
  paperId: string,
): readonly (readonly unknown[])[] {
  if (kind !== 'note') assertSettledStreamPolicy(kind);
  const artifactKey = kind === 'note'
    ? artifactKeys.note(paperId)
    : kind === 'explainer'
      ? artifactKeys.explainer(paperId)
      : artifactKeys.translation(paperId);
  return [artifactKey, paperKeys.list(), paperKeys.detail(paperId)];
}
