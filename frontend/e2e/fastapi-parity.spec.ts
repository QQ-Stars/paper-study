import { spawn, type ChildProcessWithoutNullStreams } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createInterface } from 'node:readline';

import { expect, test } from '@playwright/test';

interface FastApiProcess {
  readonly baseUrl: string;
  readonly databasePath: string;
  stop(): Promise<void>;
}

const repositoryRoot = fileURLToPath(new URL('../../', import.meta.url));
const pythonExecutable = fileURLToPath(
  new URL('../../.venv/Scripts/python.exe', import.meta.url),
);

function waitForExit(child: ChildProcessWithoutNullStreams): Promise<number | null> {
  return new Promise((resolve, reject) => {
    child.once('error', reject);
    child.once('exit', resolve);
  });
}

async function startFastApi(): Promise<FastApiProcess> {
  const child = spawn(
    pythonExecutable,
    ['-B', '-m', 'backend.tests.support.fastapi_e2e_server'],
    {
      cwd: repositoryRoot,
      env: {
        ...process.env,
        API_PROCESS_ROLE: 'api',
        OCR_ENABLED: '0',
        OBSIDIAN_ENABLED: '0',
        PYTHONDONTWRITEBYTECODE: '1',
      },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
    },
  );
  let stderr = '';
  child.stderr.setEncoding('utf8');
  child.stderr.on('data', (chunk: string) => {
    stderr += chunk;
  });
  const lines = createInterface({ input: child.stdout });
  const ready = new Promise<{ baseUrl: string; databasePath: string }>((resolve, reject) => {
    const timer = setTimeout(() => {
      reject(new Error(`FastAPI fixture readiness timed out.\n${stderr}`));
    }, 30_000);
    lines.on('line', (line) => {
      try {
        const event = JSON.parse(line) as {
          event?: string;
          baseUrl?: string;
          database?: string;
        };
        if (event.event === 'ready' && event.baseUrl && event.database) {
          clearTimeout(timer);
          resolve({ baseUrl: event.baseUrl, databasePath: event.database });
        }
      } catch {
        // Alembic may emit ordinary setup lines before the readiness record.
      }
    });
    child.once('exit', (code) => {
      clearTimeout(timer);
      reject(new Error(`FastAPI fixture exited before readiness (${code}).\n${stderr}`));
    });
    child.once('error', (error) => {
      clearTimeout(timer);
      reject(error);
    });
  });
  const { baseUrl, databasePath } = await ready;
  return {
    baseUrl,
    databasePath,
    async stop() {
      child.stdin.end('shutdown\n');
      const code = await waitForExit(child);
      lines.close();
      if (code !== 0) {
        throw new Error(`FastAPI fixture exited with ${code}.\n${stderr}`);
      }
    },
  };
}

let fastApi: FastApiProcess;

test.beforeAll(async () => {
  fastApi = await startFastApi();
});

test.afterAll(async () => {
  await fastApi.stop();
});

test('FastAPI parity serves the real workspace and legacy flows', async ({ page }) => {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];
  const requestUrls: string[] = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('pageerror', (error) => pageErrors.push(error.message));
  page.on('request', (request) => requestUrls.push(request.url()));

  expect(fastApi.databasePath.toLowerCase()).not.toBe(
    fileURLToPath(new URL('../../data/app.db', import.meta.url)).toLowerCase(),
  );
  await page.goto(`${fastApi.baseUrl}/workspace/library`);
  await expect(page.getByRole('heading', { level: 1, name: '文献库' })).toBeVisible();
  const table = page.getByRole('table', { name: '文献台账' });
  const paperRow = table.getByRole('row', { name: /FastAPI Parity Paper/ });
  await expect(paperRow).toContainText('PDF');
  await expect(paperRow).toContainText('NOTE');
  await paperRow.dblclick();

  await expect(page).toHaveURL(/\/workspace\/reader\/paper-1$/);
  await expect(
    page.getByRole('heading', { level: 1, name: 'FastAPI 端到端校验论文' }),
  ).toBeVisible();
  const pdfWorkspace = page.getByRole('region', { name: 'PDF 阅读工作区' });
  await expect(pdfWorkspace.getByRole('article', { name: '第 1 页' }))
    .toHaveAttribute('data-status', 'ready');
  await expect(page.locator('.pdf-page__text-layer'))
    .toContainText('FastAPI parity deterministic PDF fixture');

  const artifacts = page.getByRole('region', { name: '论文阅读工作台' });
  await artifacts.getByRole('tab', { name: '笔记' }).click();
  const note = artifacts.getByRole('textbox', { name: '笔记内容' });
  await note.fill('# FastAPI E2E note\n\nPersisted through the candidate API.');
  const noteWrite = page.waitForResponse((response) => (
    response.url() === `${fastApi.baseUrl}/api/note`
      && response.request().method() === 'POST'
  ));
  await artifacts.getByRole('button', { name: '保存笔记' }).click();
  await expect((await noteWrite).ok()).toBe(true);
  await expect(artifacts.getByRole('status').filter({ hasText: '笔记已保存' })).toBeVisible();

  await artifacts.getByRole('tab', { name: '讲解' }).click();
  const generation = page.waitForResponse((response) => (
    response.url() === `${fastApi.baseUrl}/api/explain`
      && response.request().method() === 'POST'
  ));
  await artifacts.getByRole('button', { name: '生成讲解' }).click();
  await expect((await generation).ok()).toBe(true);
  await expect(artifacts.getByText('The isolated provider returned this deterministic result.'))
    .toBeVisible();

  const pdfResponse = await page.request.get(`${fastApi.baseUrl}/pdfbytes?id=paper-1`);
  expect(pdfResponse.status()).toBe(200);
  expect(Buffer.from(await pdfResponse.body()).subarray(0, 8).toString('ascii'))
    .toBe('%PDF-1.4');

  await page.goto(`${fastApi.baseUrl}/workspace/reviews`);
  const overdue = page.getByRole('region', { name: '逾期复习' });
  await expect(overdue).toContainText('FastAPI 端到端校验论文');
  const reviewWrite = page.waitForResponse((response) => (
    response.url() === `${fastApi.baseUrl}/api/reviews/complete`
      && response.request().method() === 'POST'
  ));
  await overdue.getByRole('button', { name: '完成本轮' }).click();
  await expect((await reviewWrite).ok()).toBe(true);
  await expect(page.getByRole('status').filter({ hasText: '已完成 paper-1' })).toBeVisible();

  await page.goto(`${fastApi.baseUrl}/legacy/`);
  const legacyRow = page.locator('#homeBody tr[data-id="paper-1"]');
  await expect(legacyRow).toContainText('FastAPI Parity Paper');
  await legacyRow.click();
  await expect(page.locator('#paperTitle')).toContainText('FastAPI Parity Paper');
  await expect(page.locator('#layout')).toBeVisible();

  expect(requestUrls.some((url) => url.startsWith(`${fastApi.baseUrl}/api/papers`))).toBe(true);
  expect(requestUrls.some((url) => url.startsWith(`${fastApi.baseUrl}/api/paper/get`))).toBe(true);
  expect(requestUrls.some((url) => /127\.0\.0\.1:517[34]/.test(url))).toBe(false);
  expect(consoleErrors).toEqual([]);
  expect(pageErrors).toEqual([]);
});
