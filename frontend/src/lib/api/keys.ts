export const paperKeys = {
  all: () => ['papers'] as const,
  list: () => ['papers', 'list'] as const,
  detail: (paperId: string) => ['papers', 'detail', paperId] as const,
};

export const artifactKeys = {
  all: (paperId: string) => ['papers', paperId, 'artifacts'] as const,
  note: (paperId: string) => ['papers', paperId, 'artifacts', 'note'] as const,
  explainer: (paperId: string) => ['papers', paperId, 'artifacts', 'explainer'] as const,
  translation: (paperId: string) => ['papers', paperId, 'artifacts', 'translation'] as const,
};

export const reviewKeys = {
  all: () => ['reviews'] as const,
  snapshot: () => ['reviews', 'snapshot'] as const,
  list: () => ['reviews', 'snapshot'] as const,
};

export const titleTranslationKeys = {
  status: () => ['title-translations', 'status'] as const,
};

export const jobKeys = {
  all: () => ['jobs'] as const,
  list: () => ['jobs', 'list'] as const,
  detail: (jobId: number) => ['jobs', 'detail', jobId] as const,
};

export const scheduleKeys = {
  all: () => ['schedules'] as const,
  list: () => ['schedules', 'list'] as const,
};

export const pdfKeys = {
  all: () => ['pdf'] as const,
  status: (paperId: string) => ['pdf', 'status', paperId] as const,
  scan: (directory: string) => ['pdf', 'scan', directory] as const,
};

export const settingsKeys = {
  view: () => ['settings', 'view'] as const,
};

export const obsidianKeys = {
  all: () => ['obsidian'] as const,
  status: () => ['obsidian', 'status'] as const,
  global: () => ['obsidian', 'global'] as const,
  paper: (paperId: string) => ['obsidian', 'paper', paperId] as const,
};

export const citationKeys = {
  graph: () => ['citation-graph'] as const,
};

export const queryKeys = {
  papers: paperKeys,
  artifacts: artifactKeys,
  reviews: reviewKeys,
  titleTranslations: titleTranslationKeys,
  jobs: jobKeys,
  schedules: scheduleKeys,
  pdf: pdfKeys,
  settings: settingsKeys,
  obsidian: obsidianKeys,
  citationGraph: citationKeys,
};
