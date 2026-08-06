export const streamSideEffectPolicies = {
  titleTranslations: { reconcileOn: 'settled', facts: ['papers', 'title-translation-status'] },
  explain: { reconcileOn: 'settled', facts: ['explainer', 'papers'] },
  explainBatch: { reconcileOn: 'settled', facts: ['explainers', 'papers'] },
  translate: { reconcileOn: 'settled', facts: ['translation', 'papers'] },
  embed: { reconcileOn: 'settled', facts: ['semantic-index'] },
  importPdfs: { reconcileOn: 'settled', facts: ['papers'] },
  downloadPdfs: { reconcileOn: 'settled', facts: ['papers', 'pdf-status'] },
  normalizeVenues: { reconcileOn: 'settled', facts: ['papers'] },
  citationBuild: { reconcileOn: 'settled', facts: ['citation-graph'] },
  ingestSelected: { reconcileOn: 'settled', facts: ['papers'] },
  jobsConfirm: { reconcileOn: 'settled', facts: ['papers', 'jobs', 'job-detail'] },
} as const;
