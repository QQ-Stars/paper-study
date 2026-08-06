import type { Page, TestInfo } from '@playwright/test';

import { installRuntimeAudit } from './fixtures/runtimeAudit';
import { expect, test } from './fixtures/mockApi';

function recordApprovedPrototype(testInfo: TestInfo, filename: string): void {
  testInfo.annotations.push({
    type: 'approved-prototype',
    description: `.superpowers/brainstorm/20260804-react-cleanroom/content/${filename}`,
  });
}

async function settleVisualState(page: Page): Promise<void> {
  await page.evaluate(() => document.fonts.ready);
  await page.waitForTimeout(150);
}

test.describe('Visual regression surfaces', () => {
  test('Desktop Dashboard · 1440×900', async ({ page }, testInfo) => {
    recordApprovedPrototype(testInfo, 'prototype-dashboard-desktop.png');
    const audit = await installRuntimeAudit(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/workspace/dashboard');
    await expect(page.getByRole('listbox', { name: '论文甲板' })).toBeVisible();
    await expect(page.getByRole('complementary', { name: '论文上下文' })).toBeVisible();
    const timeline = page.getByRole('region', { name: '研究时间线' });
    await expect(timeline).toHaveCount(1);
    await expect(timeline).toBeVisible();
    await settleVisualState(page);
    await expect(page).toHaveScreenshot('dashboard-desktop-1440x900.png');
    await audit.assertClean();
  });

  test('Desktop Library · 1440×900', async ({ page }, testInfo) => {
    recordApprovedPrototype(testInfo, 'prototype-library.png');
    const audit = await installRuntimeAudit(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/workspace/library');
    await expect(page.getByRole('table', { name: '文献台账' })).toBeVisible();
    const preview = page.getByRole('complementary', { name: '论文预览' });
    await expect(preview).toBeVisible();
    const [filtersBox, mainBox, previewBox] = await Promise.all([
      page.locator('.library-filters').boundingBox(),
      page.locator('#workspace-main').boundingBox(),
      preview.boundingBox(),
    ]);
    expect(filtersBox).not.toBeNull();
    expect(mainBox).not.toBeNull();
    expect(previewBox).not.toBeNull();
    expect((filtersBox?.x ?? 0) + (filtersBox?.width ?? 0))
      .toBeLessThanOrEqual((mainBox?.x ?? 0) + (mainBox?.width ?? 0));
    expect((mainBox?.x ?? 0) + (mainBox?.width ?? 0))
      .toBeLessThanOrEqual(previewBox?.x ?? 0);
    await settleVisualState(page);
    await expect(page).toHaveScreenshot('library-desktop-1440x900.png');
    await audit.assertClean();
  });

  test('Desktop Reader · 1440×900', async ({ page }, testInfo) => {
    recordApprovedPrototype(testInfo, 'prototype-reader.png');
    const audit = await installRuntimeAudit(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/workspace/reader/paper-lifecycle');
    const pdfPage = page.getByRole('article', { name: '第 1 页' });
    await expect(pdfPage).toHaveAttribute('data-status', 'ready');
    await expect(pdfPage).toHaveCSS('background-color', 'rgb(244, 242, 237)');
    await settleVisualState(page);
    await expect(page).toHaveScreenshot('reader-desktop-1440x900.png');
    await audit.assertClean({ requireWorker: true });
  });

  test('Desktop Acquire · 1440×900', async ({ page }, testInfo) => {
    recordApprovedPrototype(testInfo, 'prototype-ingest-jobs.png');
    const audit = await installRuntimeAudit(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/workspace/acquire');
    await page.getByRole('textbox', { name: '研究方向' }).fill('lifecycle-safe research reader');
    await page.getByRole('button', { name: '开始检索' }).click();
    await expect(page.getByText('Deterministic Async Ownership in React')).toBeVisible();
    await settleVisualState(page);
    await expect(page).toHaveScreenshot('acquire-desktop-1440x900.png');
    await audit.assertClean();
  });

  test('Desktop Jobs · 1440×900', async ({ mockApi, page }, testInfo) => {
    recordApprovedPrototype(testInfo, 'prototype-ingest-jobs.png');
    const audit = await installRuntimeAudit(page);
    mockApi.useJobs('review');
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/workspace/jobs/2');
    await expect(page.getByRole('region', { name: '后台任务列表' })).toBeVisible();
    const detail = page.getByRole('region', { name: '任务 2 详情' });
    await expect(detail).toBeVisible();
    await expect(detail.getByText('Deterministic Async Ownership in React')).toBeVisible();
    await expect(page.getByRole('region', { name: '定时计划' })).toBeVisible();
    await settleVisualState(page);
    await expect(page).toHaveScreenshot('jobs-desktop-1440x900.png');
    await audit.assertClean();
  });

  test('Mobile Dashboard · 390×844', async ({ page }, testInfo) => {
    recordApprovedPrototype(testInfo, 'prototype-dashboard-mobile.png');
    const audit = await installRuntimeAudit(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/workspace/dashboard');
    await expect(page.getByRole('listbox', { name: '论文甲板' })).toBeVisible();
    await expect(page.getByRole('dialog', { name: '论文上下文' })).toHaveCount(0);
    await settleVisualState(page);
    await expect(page).toHaveScreenshot('dashboard-mobile-sheet-closed-390x844.png');
    await page.getByRole('button', { name: '论文上下文', exact: true }).click();
    await expect(page.getByRole('dialog', { name: '论文上下文' })).toBeVisible();
    await expect(page.locator('.workspace-overlay')).toHaveAttribute('data-presentation', 'sheet');
    await settleVisualState(page);
    await expect(page).toHaveScreenshot('dashboard-mobile-sheet-open-390x844.png');
    await audit.assertClean();
  });

  test('Dashboard responsive boundaries · 1100 / 900 / 760', async ({ page }) => {
    const audit = await installRuntimeAudit(page);
    const trigger = page.getByRole('button', { name: '论文上下文', exact: true });

    await page.setViewportSize({ width: 1100, height: 900 });
    await page.goto('/workspace/dashboard');
    await expect(page.getByRole('complementary', { name: '论文上下文' })).toBeVisible();
    await expect(trigger).toBeHidden();
    await settleVisualState(page);
    await expect(page).toHaveScreenshot('dashboard-rail-boundary-1100x900.png');

    await page.setViewportSize({ width: 900, height: 900 });
    await trigger.click();
    await expect(page.locator('.workspace-overlay')).toHaveAttribute('data-presentation', 'drawer');
    await settleVisualState(page);
    await expect(page).toHaveScreenshot('dashboard-drawer-open-900x900.png');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: '论文上下文' })).toHaveCount(0);

    await page.setViewportSize({ width: 760, height: 844 });
    await trigger.click();
    await expect(page.locator('.workspace-overlay')).toHaveAttribute('data-presentation', 'sheet');
    await settleVisualState(page);
    await expect(page).toHaveScreenshot('dashboard-sheet-boundary-open-760x844.png');
    await audit.assertClean();
  });
});
