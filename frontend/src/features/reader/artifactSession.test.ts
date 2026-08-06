import { describe, expect, it } from 'vitest';

import {
  artifactReconciliationKeys,
  commandForIdentity,
  emptyArtifactSession,
  normalizeArtifactText,
  reduceArtifactSession,
  type ArtifactCommandOwner,
} from './artifactSession';

const owner: ArtifactCommandOwner = {
  paperId: 'paper-a',
  generation: 3,
  runId: 7,
};

describe('artifact session invariants', () => {
  it('treats blank payloads and the legacy explainer sentinel as true empty states', () => {
    expect(normalizeArtifactText('')).toBeNull();
    expect(normalizeArtifactText('  \n')).toBeNull();
    expect(normalizeArtifactText('*(暂无讲解)*')).toBeNull();
    expect(normalizeArtifactText('  # Evidence  ')).toBe('  # Evidence  ');
  });

  it('drops progress and terminal actions that do not own the running identity', () => {
    const started = reduceArtifactSession(emptyArtifactSession(), {
      type: 'start',
      kind: 'explainer',
      owner,
    });
    const staleOwner = { ...owner, generation: 4 };

    expect(reduceArtifactSession(started, {
      type: 'progress',
      kind: 'explainer',
      owner: staleOwner,
      line: 'late progress',
    })).toBe(started);
    expect(reduceArtifactSession(started, {
      type: 'success',
      kind: 'explainer',
      owner: staleOwner,
    })).toBe(started);
    expect(commandForIdentity(started.explainer, 'paper-b', 4).status).toBe('idle');
  });

  it('maps each settled command to one fixed artifact plus precise paper facts', () => {
    expect(artifactReconciliationKeys('explainer', 'paper-a')).toEqual([
      ['papers', 'paper-a', 'artifacts', 'explainer'],
      ['papers', 'list'],
      ['papers', 'detail', 'paper-a'],
    ]);
    expect(artifactReconciliationKeys('translation', 'paper-b')[0]).toEqual(
      ['papers', 'paper-b', 'artifacts', 'translation'],
    );
    expect(artifactReconciliationKeys('note', 'paper-c')[0]).toEqual(
      ['papers', 'paper-c', 'artifacts', 'note'],
    );
  });
});
