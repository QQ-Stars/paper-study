import type { Locator, Page } from '@playwright/test';

import { installRuntimeAudit } from './fixtures/runtimeAudit';
import { expect, test } from './fixtures/mockApi';

async function expectTouchTarget(locator: Locator): Promise<void> {
  const box = await locator.boundingBox();
  expect(box, `visible touch target for ${await locator.getAttribute('aria-label') ?? 'control'}`).not.toBeNull();
  expect(box?.width ?? 0).toBeGreaterThanOrEqual(44);
  expect(box?.height ?? 0).toBeGreaterThanOrEqual(44);
}

async function emulateReducedTransparencyAndMotion(page: Page): Promise<void> {
  const session = await page.context().newCDPSession(page);
  await session.send('Emulation.setEmulatedMedia', {
    features: [
      { name: 'prefers-reduced-motion', value: 'reduce' },
      { name: 'prefers-reduced-transparency', value: 'reduce' },
    ],
  });
}

test.describe('Workspace accessibility and responsive behavior', () => {
  test('exposes a visible Skip Link and moves focus to route titles at 1440×900', async ({
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto('/workspace/dashboard');

    const skip = page.getByRole('link', { name: '跳到主要内容' });
    await expect(skip).toHaveJSProperty('tabIndex', 0);
    await skip.focus();
    await expect(skip).toBeFocused();
    await expect(skip).toBeVisible();
    const outline = await skip.evaluate((element) => getComputedStyle(element).outlineStyle);
    expect(outline).not.toBe('none');
    await page.keyboard.press('Enter');
    await expect(page.locator('#workspace-main')).toBeFocused();

    await page.getByRole('link', { name: '文献库' }).click();
    await expect(page.getByRole('heading', { level: 1, name: '文献库' })).toBeFocused();
    await audit.assertClean();
  });

  test('traps focus in the 900px inspector drawer and restores it after Escape', async ({
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.setViewportSize({ width: 900, height: 900 });
    await page.goto('/workspace/dashboard');

    const trigger = page.getByRole('button', { name: '论文上下文', exact: true });
    await trigger.click();
    const dialog = page.getByRole('dialog', { name: '论文上下文' });
    await expect(dialog).toBeVisible();
    await expect(dialog).toHaveAttribute('aria-modal', 'true');
    await expect(dialog.locator(':focus')).toHaveCount(1);

    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(trigger).toBeFocused();
    await audit.assertClean();
  });

  test('keeps mobile navigation and the inspector sheet usable with 44px targets at 390×844', async ({
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/workspace/dashboard');

    const navigation = page.getByRole('navigation', { name: '全局导航' });
    await expect(navigation).toBeVisible();
    for (const name of ['今日', '文献库', '复习', '采集', '任务', '洞察', '设置']) {
      await expectTouchTarget(navigation.getByRole('link', { name }));
    }

    const primaryAction = page.getByRole('button', { name: '打开阅读', exact: true });
    await expectTouchTarget(primaryAction);
    const [primaryActionBox, navigationBox] = await Promise.all([
      primaryAction.boundingBox(),
      navigation.boundingBox(),
    ]);
    expect(primaryActionBox).not.toBeNull();
    expect(navigationBox).not.toBeNull();
    expect((primaryActionBox?.y ?? 0) + (primaryActionBox?.height ?? 0))
      .toBeLessThanOrEqual(navigationBox?.y ?? 0);

    const inspectorTrigger = page.getByRole('button', { name: '论文上下文', exact: true });
    await expectTouchTarget(inspectorTrigger);
    await inspectorTrigger.click();
    const dialog = page.getByRole('dialog', { name: '论文上下文' });
    await expect(dialog).toBeVisible();
    await expect(page.locator('.workspace-overlay')).toHaveAttribute('data-presentation', 'sheet');
    await expect(navigation).toBeVisible();
    await expectTouchTarget(dialog.getByRole('button', { name: '关闭论文上下文' }));

    await page.keyboard.press('Escape');
    await expect(dialog).toHaveCount(0);
    await expect(inspectorTrigger).toBeFocused();
    await audit.assertClean();
  });

  test('switches the inspector rail, drawer, and sheet at the exact responsive boundaries', async ({
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    const trigger = page.getByRole('button', { name: '论文上下文', exact: true });

    await page.setViewportSize({ width: 1100, height: 900 });
    await page.goto('/workspace/dashboard');
    await expect(page.getByRole('complementary', { name: '论文上下文' })).toBeVisible();
    await expect(trigger).toBeHidden();

    await page.setViewportSize({ width: 1099, height: 900 });
    await expect(page.getByRole('complementary', { name: '论文上下文' })).toHaveCount(0);
    await trigger.click();
    await expect(page.locator('.workspace-overlay')).toHaveAttribute('data-presentation', 'drawer');
    await page.keyboard.press('Escape');

    await page.setViewportSize({ width: 761, height: 900 });
    await trigger.click();
    await expect(page.locator('.workspace-overlay')).toHaveAttribute('data-presentation', 'drawer');
    await page.keyboard.press('Escape');

    await page.setViewportSize({ width: 760, height: 844 });
    await trigger.click();
    await expect(page.locator('.workspace-overlay')).toHaveAttribute('data-presentation', 'sheet');
    await page.keyboard.press('Escape');
    await audit.assertClean();
  });

  test('honors reduced motion and reduced transparency in overlay presentation', async ({
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await emulateReducedTransparencyAndMotion(page);
    await page.setViewportSize({ width: 900, height: 900 });
    await page.goto('/workspace/dashboard');

    await page.getByRole('button', { name: /搜索或运行命令/ }).click();
    const dialog = page.getByRole('dialog', { name: '命令栏' });
    await expect(dialog).toHaveAttribute('data-motion', 'reduced');
    await expect(dialog).toHaveAttribute('data-transparency', 'reduced');
    const presentation = await dialog.evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        animationDuration: style.animationDuration,
        backdropFilter: style.backdropFilter,
        webkitBackdropFilter: style.getPropertyValue('-webkit-backdrop-filter'),
      };
    });
    const longestAnimationSeconds = Math.max(
      ...presentation.animationDuration.split(',').map((duration) => Number.parseFloat(duration)),
    );
    expect(longestAnimationSeconds).toBeLessThanOrEqual(0.01);
    expect([presentation.backdropFilter, presentation.webkitBackdropFilter])
      .not.toContain('blur(18px) saturate(1.12)');
    await audit.assertClean();
  });
});
