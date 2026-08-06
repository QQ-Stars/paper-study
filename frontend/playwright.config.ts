import { fileURLToPath } from 'node:url';

import { defineConfig, devices } from '@playwright/test';

const frontendDirectory = fileURLToPath(new URL('.', import.meta.url));
const isContinuousIntegration = Boolean(process.env.CI);
const devCommand = 'node ./e2e/fixtures/viteHarness.mjs';

export default defineConfig({
  testDir: './e2e',
  globalTeardown: './e2e/fixtures/viteHarness.mjs',
  outputDir: './test-results',
  snapshotPathTemplate: '{testDir}/__screenshots__/{arg}{ext}',
  fullyParallel: true,
  forbidOnly: isContinuousIntegration,
  retries: isContinuousIntegration ? 1 : 0,
  workers: 4,
  reporter: isContinuousIntegration
    ? [['line'], ['html', { open: 'never', outputFolder: 'playwright-report' }]]
    : [['list'], ['html', { open: 'never', outputFolder: 'playwright-report' }]],
  timeout: 30_000,
  expect: {
    timeout: 7_500,
    toHaveScreenshot: {
      animations: 'disabled',
      caret: 'hide',
      maxDiffPixelRatio: 0.01,
    },
  },
  use: {
    ...devices['Desktop Chrome'],
    baseURL: 'http://127.0.0.1:5174/workspace/',
    colorScheme: 'dark',
    locale: 'zh-CN',
    timezoneId: 'Asia/Shanghai',
    viewport: { width: 1440, height: 900 },
    serviceWorkers: 'block',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: devCommand,
    cwd: frontendDirectory,
    url: 'http://127.0.0.1:5174/workspace/',
    reuseExistingServer: false,
    timeout: 120_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
});
