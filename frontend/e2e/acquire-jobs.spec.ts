import { installRuntimeAudit } from './fixtures/runtimeAudit';
import { expect, test } from './fixtures/mockApi';

test.describe('Acquire and background job workflows', () => {
  test('searches, verifies, and ingests a partial candidate set with real NDJSON frames', async ({
    mockApi,
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.goto('/workspace/acquire');

    await page.getByRole('textbox', { name: '研究方向' }).fill('lifecycle safe document readers');
    await page.getByRole('button', { name: '开始检索' }).click();
    await expect(page.getByText('STAGE::search')).toBeVisible();
    const first = page.getByRole('checkbox', { name: '选择 Deterministic Async Ownership in React' });
    const existing = page.getByRole('checkbox', { name: '选择 Clean-Room Research Workspaces' });
    await expect(first).toBeChecked();
    await expect(existing).toBeDisabled();
    await expect(page.getByText('已在库')).toBeVisible();

    await page.getByRole('button', { name: '核验会议信息' }).click();
    await expect(page.getByText('已核实')).toBeVisible();
    await page.getByRole('button', { name: '入库选中项' }).click();
    await expect(page.getByText('服务器确认新增 1 篇。')).toBeVisible();

    expect(mockApi.lastRequest('/api/search', 'POST')?.body).toMatchObject({
      query: 'lifecycle safe document readers',
    });
    expect(mockApi.lastRequest('/api/verify-venue', 'POST')?.body).toMatchObject({
      sources: ['dblp', 'semanticscholar'],
    });
    const ingest = mockApi.lastRequest('/api/ingest-selected', 'POST')?.body;
    expect(ingest).toMatchObject({ downloadPdf: true });
    expect(ingest).toHaveProperty('candidates');
    await audit.assertClean();
  });

  test('stops receiving a pending stream honestly and recovers from a protocol failure', async ({
    mockApi,
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    mockApi.streamNext('/api/search', {
      delayMs: 900,
      frames: [
        { type: 'progress', line: 'LATE::search' },
        { type: 'result', ok: true, candidates: [] },
      ],
    });
    await page.goto('/workspace/acquire');
    await page.getByRole('textbox', { name: '研究方向' }).fill('cancelled search');
    await page.getByRole('button', { name: '开始检索' }).click();
    await expect.poll(() => mockApi.requestCount('/api/search', 'POST')).toBe(1);
    await page.getByRole('button', { name: '停止接收' }).click();
    await expect(page.getByText('已停止接收；服务端可能仍在运行。')).toBeVisible();

    mockApi.streamNext('/api/search', {
      frames: [
        { type: 'progress', line: 'BROKEN::protocol' },
        { type: 'unexpected-terminal', ok: true },
      ],
    });
    await page.getByRole('button', { name: '重试检索' }).click();
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.getByText('BROKEN::protocol')).toBeVisible();

    await page.getByRole('button', { name: '重试检索' }).click();
    await expect(page.getByText('Deterministic Async Ownership in React')).toBeVisible();
    expect(mockApi.requestCount('/api/search', 'POST')).toBe(3);
    await audit.assertClean();
  });

  test('reports local PDF scan, import, and download outcomes without hiding partial success', async ({
    mockApi,
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.goto('/workspace/acquire');

    const local = page.getByRole('region', { name: '本地 PDF' });
    await local.getByRole('textbox', { name: 'PDF 文件夹' }).fill('F:/fixture/inbox');
    await local.getByRole('button', { name: '扫描文件夹' }).click();
    await expect(local.getByText('TOTAL 2')).toBeVisible();
    await expect(local.getByRole('checkbox', { name: '选择 fixture-one.pdf' })).toBeChecked();

    await local.getByRole('button', { name: '导入选中 PDF' }).click();
    await expect(local.getByText('PARSED 2 · ADDED 1 · DUP 1 · SKIP 0')).toBeVisible();
    await local.getByRole('button', { name: '补齐馆藏 PDF' }).click();
    await expect(local.getByText('TOTAL 3 · DOWNLOADED 2 · SKIP 1 · FAILED 0')).toBeVisible();

    expect(mockApi.lastRequest('/api/scan-pdfs', 'GET')?.search).toContain('dir=F%3A%2Ffixture%2Finbox');
    expect(mockApi.lastRequest('/api/import-pdfs', 'POST')?.body).toMatchObject({ enrich: true });
    await audit.assertClean();
  });

  test('shows a true zero state, creates a job, confirms candidates, and uses server schedule facts', async ({
    mockApi,
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    mockApi.useJobs('empty');
    await page.goto('/workspace/jobs');
    await expect(page.getByText('当前没有后台任务')).toBeVisible();

    await page.getByRole('textbox', { name: '后台研究方向' }).fill('deep research modules');
    await page.getByRole('button', { name: '创建后台任务' }).click();
    await expect(page).toHaveURL(/\/workspace\/jobs\/20$/);
    await expect(page.getByRole('region', { name: '任务 20 详情' })).toBeVisible();
    expect(mockApi.lastRequest('/api/jobs', 'POST')?.body).toMatchObject({
      query: 'deep research modules',
    });

    mockApi.useJobs('review');
    await page.goto('/workspace/jobs/2');
    const detail = page.getByRole('region', { name: '任务 2 详情' });
    await expect(detail.getByText('Deterministic Async Ownership in React')).toBeVisible();
    await detail.getByRole('button', { name: '确认选中候选' }).click();
    await expect(detail.getByText('服务器确认新增 1 篇。')).toBeVisible();
    expect(mockApi.lastRequest('/api/jobs/confirm', 'POST')?.body).toMatchObject({
      jobId: 2,
      downloadPdf: true,
    });

    const schedules = page.getByRole('region', { name: '定时计划' });
    await expect(schedules.getByText('上次：2026-08-01T08:00:00.000Z')).toBeVisible();
    await expect(schedules.getByText('下次：2026-08-08T08:00:00.000Z')).toBeVisible();
    await schedules.getByRole('button', { name: '启用计划 7' }).click();
    await expect(schedules.getByText('已启用')).toBeVisible();
    expect(mockApi.lastRequest('/api/schedules/toggle', 'POST')?.body).toEqual({
      id: 7,
      enabled: true,
    });
    await audit.assertClean();
  });
});
