import { installRuntimeAudit } from './fixtures/runtimeAudit';
import { expect, test } from './fixtures/mockApi';

test.describe('React research workspace workflows', () => {
  test('moves through the paper deck with the keyboard and opens the selected URL', async ({
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.goto('/workspace/dashboard');

    await expect(page.getByRole('heading', { level: 1, name: '研究概览' })).toBeVisible();
    const deck = page.getByRole('listbox', { name: '论文甲板' });
    await expect(deck).toBeVisible();
    const first = deck.getByRole('option', { name: /Lifecycle-Safe Research Readers/ });
    const second = deck.getByRole('option', { name: /Bounded Workers for Scholarly Documents/ });
    await expect(first).toHaveAttribute('aria-selected', 'true');

    await first.focus();
    await page.keyboard.press('ArrowRight');
    await expect(second).toBeFocused();
    await expect(second).toHaveAttribute('aria-selected', 'true');
    await page.keyboard.press('Enter');

    await expect(page).toHaveURL(/\/workspace\/reader\/paper-workers$/);
    await expect(page.getByRole('heading', { level: 1, name: '阅读' })).toBeVisible();
    await page.goBack();
    await expect(page).toHaveURL(/\/workspace\/dashboard$/);
    await expect(deck).toBeVisible();
    await audit.assertClean();
  });

  test('filters the library and persists fixed-paper mutations through the API boundary', async ({
    mockApi,
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.goto('/workspace/library');

    const table = page.getByRole('table', { name: '文献台账' });
    await expect(table.getByRole('row', { name: /Lifecycle-Safe Research Readers/ })).toBeVisible();
    const search = page.getByRole('searchbox', { name: '搜索文献' });
    await search.fill('Bounded Workers');
    await expect(table.getByRole('row', { name: /Bounded Workers for Scholarly Documents/ })).toBeVisible();
    await expect(table.getByRole('row', { name: /Lifecycle-Safe Research Readers/ })).toHaveCount(0);
    await search.clear();

    const lifecycleRow = table.getByRole('row', { name: /Lifecycle-Safe Research Readers/ });
    await lifecycleRow.getByRole('button', { name: '取消收藏 Lifecycle-Safe Research Readers' }).click();
    await lifecycleRow.getByRole('button', {
      name: 'Lifecycle-Safe Research Readers 当前学习状态 学习中，切换到 已理解',
    }).click();

    await expect.poll(() => mockApi.requestCount('/api/favorite', 'POST')).toBe(1);
    await expect.poll(() => mockApi.requestCount('/api/progress', 'POST')).toBe(1);
    expect(mockApi.lastRequest('/api/favorite', 'POST')?.body).toEqual({
      id: 'paper-lifecycle',
      favorite: false,
    });
    expect(mockApi.lastRequest('/api/progress', 'POST')?.body).toEqual({
      id: 'paper-lifecycle',
      status: '已理解',
    });

    await page.getByRole('button', { name: '添加论文' }).click();
    const editor = page.getByRole('dialog', { name: '添加论文' });
    await editor.getByRole('textbox', { name: '英文题名' }).fill('A New Deterministic Research Paper');
    await editor.getByRole('button', { name: '保存论文' }).click();
    await expect(table.getByRole('row', { name: /A New Deterministic Research Paper/ })).toBeVisible();
    expect(mockApi.lastRequest('/api/paper/add', 'POST')?.body).toMatchObject({
      title: 'A New Deterministic Research Paper',
    });
    await audit.assertClean();
  });

  test('completes an authoritative review step and opens the same paper in Reader', async ({
    mockApi,
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.goto('/workspace/reviews');

    const overdue = page.getByRole('region', { name: '逾期复习' });
    await expect(overdue.getByText('Lifecycle-Safe Research Readers')).toBeVisible();
    await overdue.getByRole('button', { name: '完成本轮' }).click();
    await expect(page.getByRole('status').filter({ hasText: 'paper-lifecycle' }))
      .toContainText('paper-lifecycle');
    await expect.poll(() => mockApi.requestCount('/api/reviews/complete', 'POST')).toBe(1);
    expect(mockApi.lastRequest('/api/reviews/complete', 'POST')?.body).toEqual({
      id: 'paper-lifecycle',
    });

    await overdue.getByRole('button', { name: '打开阅读' }).click();
    await expect(page).toHaveURL(/\/workspace\/reader\/paper-lifecycle$/);
    await audit.assertClean();
  });

  test('reads evidence-backed insights and rebuilds the citation graph explicitly', async ({
    mockApi,
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.goto('/workspace/insights');

    await expect(page.getByText('年度轨迹')).toBeVisible();
    await expect(page.getByText('主题结构')).toBeVisible();
    await expect(page.getByText('引用网络')).toBeVisible();
    const node = page.getByRole('button', { name: /Lifecycle-Safe Research Readers/ });
    await node.click();
    await expect(page).toHaveURL(/\/workspace\/reader\/paper-lifecycle$/);
    await page.goBack();
    await expect(page).toHaveURL(/\/workspace\/insights$/);

    await page.getByRole('button', { name: '重建引用图' }).click();
    await expect(page.getByText('[BUILD] citation edges')).toBeVisible();
    await expect.poll(() => mockApi.requestCount('/api/cite-build', 'POST')).toBe(1);
    await audit.assertClean();
  });

  test('keeps local appearance separate while saving only server settings', async ({
    mockApi,
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.goto('/workspace/settings');

    const pdfDirectory = page.getByRole('textbox', { name: 'PDF 目录' });
    await pdfDirectory.fill('fixture/new-pdfs');
    await page.getByRole('button', { name: '舒适' }).click();
    await expect(page.locator('html')).toHaveAttribute('data-density', 'comfortable');

    await page.getByRole('button', { name: '测试模型连接' }).click();
    await expect(page.getByText('fixture model connection verified')).toBeVisible();
    await page.getByRole('button', { name: '保存设置' }).click();
    await expect(page.getByText('设置已由服务器确认。')).toBeVisible();

    const saved = mockApi.lastRequest('/api/settings', 'POST')?.body;
    expect(saved).toMatchObject({ pdfDir: 'fixture/new-pdfs' });
    expect(saved).not.toHaveProperty('density');
    await audit.assertClean();
  });
});
