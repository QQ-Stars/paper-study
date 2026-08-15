export const obsidianCountsFixture = {
  exported: 2,
  unchanged: 3,
  conflicts: 1,
  errors: 0,
  skipped: 4,
  userManaged: 1,
  orphaned: 2,
  deleted: 0,
} as const;

export const obsidianJobFixture = {
  id: 'job-obsidian-1',
  paperId: 'paper-fixture-1',
  jobType: 'obsidian_export',
  sourceMode: null,
  status: 'queued',
} as const;

export const obsidianJobResponseFixture = {
  job: obsidianJobFixture,
  deduplicated: false,
} as const;

export const obsidianStatusFixture = {
  enabled: true,
  vaultConfigured: true,
  writable: true,
  rootFolder: 'Research',
  pdfMode: 'copy',
  lastJob: {
    id: 'job-obsidian-previous',
    paperId: 'paper-fixture-1',
    jobType: 'obsidian_export',
    status: 'succeeded',
  },
  aggregate: obsidianCountsFixture,
} as const;

export const obsidianSyncStatusFixture = {
  ...obsidianStatusFixture,
  lastJob: {
    ...obsidianStatusFixture.lastJob,
    id: 'job-obsidian-sync-previous',
    paperId: null,
    jobType: 'obsidian_sync',
  },
} as const;

export const obsidianTestFixture = { ok: true } as const;
