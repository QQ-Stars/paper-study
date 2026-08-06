import { reactWorkspaceCsp, installRuntimeAudit } from './fixtures/runtimeAudit';
import { expect, test } from './fixtures/mockApi';

const routeExpectations = [
  ['/workspace/dashboard', '研究概览'],
  ['/workspace/library', '文献库'],
  ['/workspace/reader/paper-lifecycle', '阅读'],
  ['/workspace/reviews', '复习'],
  ['/workspace/acquire', '采集'],
  ['/workspace/jobs', '任务'],
  ['/workspace/insights', '洞察'],
  ['/workspace/settings', '设置'],
] as const;

test.describe('React clean-room runtime', () => {
  test('loads a deep Reader route under the production CSP with valid Worker, font, and MIME responses', async ({
    page,
  }) => {
    const audit = await installRuntimeAudit(page, { enforceCsp: true });
    const response = await page.goto('/workspace/reader/paper-lifecycle');
    expect(response?.headers()['content-security-policy']).toBe(reactWorkspaceCsp);

    await expect(page.getByRole('region', { name: 'PDF 阅读工作区' })).toBeVisible();
    await expect(page.getByRole('article', { name: '第 1 页' })).toHaveAttribute('data-status', 'ready');
    await expect(page.locator('.katex')).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await expect.poll(() => audit.responses.some(
      (assetResponse) => assetResponse.request().resourceType() === 'font',
    )).toBe(true);
    await audit.assertClean({ requireFont: true, requireWorker: true });
  });

  test('refreshes every deep route without loading legacy application assets', async ({ page }) => {
    const audit = await installRuntimeAudit(page);
    for (const [path, heading] of routeExpectations) {
      await page.goto(path);
      await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
      await page.reload();
      await expect(page.getByRole('heading', { level: 1, name: heading })).toBeVisible();
    }
    expect(audit.legacyRequests()).toEqual([]);
    await audit.assertClean();
  });

  test('releases PDF canvases and workers after twenty paper switches and repeated zooms', async ({
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.goto('/workspace/reader/paper-lifecycle');
    await expect(page.getByRole('article', { name: '第 1 页' })).toHaveAttribute('data-status', 'ready');

    let maximumCanvasCount = 0;
    for (let index = 0; index < 20; index += 1) {
      const paperId = index % 2 === 0 ? 'paper-workers' : 'paper-lifecycle';
      await page.evaluate((nextPaperId) => {
        globalThis.history.pushState({}, '', `/workspace/reader/${nextPaperId}`);
        globalThis.dispatchEvent(new PopStateEvent('popstate'));
      }, paperId);
      await expect(page.locator('.reader-route')).toHaveAttribute('data-paper-id', paperId);
      const reader = page.getByRole('region', { name: 'PDF 阅读工作区' });
      await expect(reader.getByRole('article', { name: '第 1 页' })).toHaveAttribute('data-status', 'ready');
      const beforeZoom = await reader.getByRole('status', { name: '当前缩放比例' }).textContent();
      await reader.getByRole('button', { name: '放大 PDF' }).click();
      await expect(reader.getByRole('status', { name: '当前缩放比例' })).not.toHaveText(beforeZoom ?? '');
      maximumCanvasCount = Math.max(maximumCanvasCount, await page.locator('.pdf-page__canvas').count());
    }
    expect(maximumCanvasCount).toBe(1);

    await page.getByRole('link', { name: '设置' }).click();
    await expect(page.getByRole('heading', { level: 1, name: '设置' })).toBeVisible();
    await expect(page.locator('.pdf-page__canvas')).toHaveCount(0);
    await expect.poll(() => audit.workers.size, { timeout: 7_500 }).toBe(0);
    await audit.assertClean({ requireWorker: true });
  });
});
