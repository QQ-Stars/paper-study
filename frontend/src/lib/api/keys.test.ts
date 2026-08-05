import { describe, expect, it } from 'vitest';

import { artifactKeys, citationKeys, jobKeys, paperKeys, pdfKeys, reviewKeys, scheduleKeys, settingsKeys, titleTranslationKeys } from './keys';

describe('stable query keys', () => {
  it('scopes entity detail and artifact keys by their fixed ids', () => {
    expect(paperKeys.list()).toEqual(['papers', 'list']);
    expect(paperKeys.detail('p1')).toEqual(['papers', 'detail', 'p1']);
    expect(artifactKeys.note('p1')).toEqual(['papers', 'p1', 'artifacts', 'note']);
    expect(artifactKeys.explainer('p1')).toEqual(['papers', 'p1', 'artifacts', 'explainer']);
    expect(artifactKeys.translation('p1')).toEqual(['papers', 'p1', 'artifacts', 'translation']);
  });

  it('covers every server-fact family used by the workspace', () => {
    expect(reviewKeys.snapshot()).toEqual(['reviews', 'snapshot']);
    expect(titleTranslationKeys.status()).toEqual(['title-translations', 'status']);
    expect(jobKeys.list()).toEqual(['jobs', 'list']);
    expect(jobKeys.detail(3)).toEqual(['jobs', 'detail', 3]);
    expect(scheduleKeys.list()).toEqual(['schedules', 'list']);
    expect(pdfKeys.status('p1')).toEqual(['pdf', 'status', 'p1']);
    expect(pdfKeys.scan('F:/papers')).toEqual(['pdf', 'scan', 'F:/papers']);
    expect(settingsKeys.view()).toEqual(['settings', 'view']);
    expect(citationKeys.graph()).toEqual(['citation-graph']);
  });
});
