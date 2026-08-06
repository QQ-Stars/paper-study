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
  await page.waitForTimeout(400);
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
    await page.getByRole('option').nth(2).dispatchEvent('click');
    await page.waitForTimeout(400);
    const [mainBox, workspaceBox, deckBox, stageBox, railBox, timelineBox, cardBoxes] = await Promise.all([
      page.locator('#workspace-main').boundingBox(),
      page.locator('.dashboard-route__workspace').boundingBox(),
      page.locator('.paper-deck').boundingBox(),
      page.locator('.paper-deck__stage').boundingBox(),
      page.locator('.workspace-context-rail').boundingBox(),
      timeline.boundingBox(),
      page.locator('.paper-deck__card').evaluateAll((cards) => cards.map((card) => {
        const box = card.getBoundingClientRect();
        return {
          x: box.x,
          y: box.y,
          width: box.width,
          height: box.height,
          selected: card.getAttribute('aria-selected') === 'true',
        };
      })),
    ]);
    expect(mainBox).not.toBeNull();
    expect(workspaceBox).not.toBeNull();
    expect(deckBox).not.toBeNull();
    expect(stageBox).not.toBeNull();
    expect(railBox).not.toBeNull();
    expect(timelineBox).not.toBeNull();
    expect(Math.abs((workspaceBox?.width ?? 0) - (deckBox?.width ?? 0))).toBeLessThanOrEqual(2);
    expect((deckBox?.width ?? 0) / (mainBox?.width ?? 1)).toBeGreaterThan(0.94);
    const cardCenters = cardBoxes
      .map((box) => ({ ...box, center: box.x + box.width / 2 }))
      .sort((left, right) => left.center - right.center);
    expect(cardCenters).toHaveLength(5);
    expect(new Set(cardCenters.map((box) => Math.round(box.center))).size).toBe(5);
    expect(
      (cardCenters.at(-1)?.center ?? 0) - (cardCenters[0]?.center ?? 0),
    ).toBeGreaterThan((stageBox?.width ?? 0) * 0.5);
    const selectedCard = cardCenters.find((box) => box.selected);
    expect(selectedCard).toBeDefined();
    expect(Math.abs(
      (selectedCard?.center ?? 0) - ((stageBox?.x ?? 0) + (stageBox?.width ?? 0) / 2),
    )).toBeLessThan((stageBox?.width ?? 0) * 0.08);
    expect(railBox?.x ?? 0).toBeGreaterThanOrEqual((mainBox?.x ?? 0) + (mainBox?.width ?? 0));
    expect(timelineBox?.y ?? 900).toBeLessThan(900);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1440);
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
    const [mobileStageBox, mobileSelectedBox] = await Promise.all([
      page.locator('.paper-deck__stage').boundingBox(),
      page.locator('.paper-deck__card[aria-selected="true"]').boundingBox(),
    ]);
    expect(mobileStageBox).not.toBeNull();
    expect(mobileSelectedBox).not.toBeNull();
    expect(mobileSelectedBox?.x ?? 0).toBeGreaterThanOrEqual(mobileStageBox?.x ?? 0);
    expect((mobileSelectedBox?.x ?? 0) + (mobileSelectedBox?.width ?? 0))
      .toBeLessThanOrEqual((mobileStageBox?.x ?? 0) + (mobileStageBox?.width ?? 0));
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
    await expect(page).toHaveScreenshot('dashboard-mobile-sheet-closed-390x844.png');
    await page.getByRole('button', { name: '论文上下文', exact: true }).click();
    await expect(page.getByRole('dialog', { name: '论文上下文' })).toBeVisible();
    await expect(page.locator('.workspace-overlay')).toHaveAttribute('data-presentation', 'sheet');
    await settleVisualState(page);
    const mobileSheetBox = await page.locator('.workspace-overlay__panel').boundingBox();
    expect(mobileSheetBox).not.toBeNull();
    expect(mobileSheetBox?.width).toBeCloseTo(390, 0);
    expect((mobileSheetBox?.y ?? 0) + (mobileSheetBox?.height ?? 0)).toBeCloseTo(844, 0);
    await expect(page).toHaveScreenshot('dashboard-mobile-sheet-open-390x844.png');
    await audit.assertClean();
  });

  test('Dashboard responsive boundaries · 1100 / 900 / 760', async ({ page }) => {
    const audit = await installRuntimeAudit(page);
    const trigger = page.getByRole('button', { name: '论文上下文', exact: true });

    await page.setViewportSize({ width: 1100, height: 900 });
    await page.goto('/workspace/dashboard');
    await expect(page.getByRole('complementary', { name: '论文上下文' })).toBeVisible();
    await expect(page.getByRole('dialog', { name: '论文上下文' })).toHaveCount(0);
    await expect(trigger).toBeHidden();
    const [railMainBox, railDeckBox] = await Promise.all([
      page.locator('#workspace-main').boundingBox(),
      page.locator('.paper-deck').boundingBox(),
    ]);
    expect(railMainBox).not.toBeNull();
    expect(railDeckBox).not.toBeNull();
    expect(railDeckBox?.x ?? 0).toBeGreaterThanOrEqual(railMainBox?.x ?? 0);
    expect((railDeckBox?.x ?? 0) + (railDeckBox?.width ?? 0))
      .toBeLessThanOrEqual((railMainBox?.x ?? 0) + (railMainBox?.width ?? 0));
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1100);
    await settleVisualState(page);
    await expect(page).toHaveScreenshot('dashboard-rail-boundary-1100x900.png');

    await page.setViewportSize({ width: 900, height: 900 });
    const drawerDeckBefore = await page.locator('.paper-deck').boundingBox();
    await trigger.click();
    await expect(page.locator('.workspace-overlay')).toHaveAttribute('data-presentation', 'drawer');
    const drawerDeckAfter = await page.locator('.paper-deck').boundingBox();
    expect(drawerDeckAfter?.x).toBeCloseTo(drawerDeckBefore?.x ?? 0, 0);
    expect(drawerDeckAfter?.width).toBeCloseTo(drawerDeckBefore?.width ?? 0, 0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(900);
    await settleVisualState(page);
    await expect(page).toHaveScreenshot('dashboard-drawer-open-900x900.png');
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: '论文上下文' })).toHaveCount(0);

    await page.setViewportSize({ width: 760, height: 844 });
    await trigger.click();
    await expect(page.locator('.workspace-overlay')).toHaveAttribute('data-presentation', 'sheet');
    await settleVisualState(page);
    const sheetBox = await page.locator('.workspace-overlay__panel').boundingBox();
    expect(sheetBox).not.toBeNull();
    expect(sheetBox?.width).toBeCloseTo(760, 0);
    expect((sheetBox?.y ?? 0) + (sheetBox?.height ?? 0)).toBeCloseTo(844, 0);
    expect(sheetBox?.height ?? 844).toBeLessThanOrEqual(844 * 0.76 + 2);
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(760);
    await expect(page).toHaveScreenshot('dashboard-sheet-boundary-open-760x844.png');
    await audit.assertClean();
  });
});
