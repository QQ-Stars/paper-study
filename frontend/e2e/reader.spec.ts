import { installRuntimeAudit } from './fixtures/runtimeAudit';
import { expect, test } from './fixtures/mockApi';

async function selectFirstPdfText(page: Parameters<typeof installRuntimeAudit>[0]) {
  const firstSpan = page.locator('.pdf-page__text-layer span').first();
  await expect(firstSpan).toHaveText(/Paper Study deterministic PDF fixture/);
  await firstSpan.evaluate((element) => {
    const selection = globalThis.getSelection();
    if (!selection) throw new Error('Selection API is unavailable');
    const range = document.createRange();
    range.selectNodeContents(element);
    selection.removeAllRanges();
    selection.addRange(range);
    document.dispatchEvent(new Event('selectionchange'));
  });
}

test.describe('Reader workflow', () => {
  test('refreshes a deep Reader URL, renders the PDF text layer, and zooms without changing identity', async ({
    mockApi,
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.goto('/workspace/reader/paper-lifecycle');

    await expect(page.getByRole('heading', { level: 2, name: '生命周期安全的研究阅读器' })).toBeVisible();
    const reader = page.getByRole('region', { name: 'PDF 阅读工作区' });
    await expect(reader.getByRole('status').filter({ hasText: '共 1 页' })).toBeVisible();
    await expect(reader.getByRole('article', { name: '第 1 页' })).toHaveAttribute('data-status', 'ready');
    await expect(page.locator('.pdf-page__text-layer')).toContainText('Paper Study deterministic PDF fixture');

    await expect(reader.getByRole('status', { name: '当前缩放比例' })).toHaveText('100%');
    await reader.getByRole('button', { name: '放大 PDF' }).click();
    await expect(reader.getByRole('status', { name: '当前缩放比例' })).toHaveText('110%');
    await expect(page.locator('.reader-route')).toHaveAttribute('data-paper-id', 'paper-lifecycle');

    await page.reload();
    await expect(page).toHaveURL(/\/workspace\/reader\/paper-lifecycle$/);
    await expect(page.getByRole('region', { name: 'PDF 阅读工作区' })).toBeVisible();
    expect(mockApi.requestCount('/api/paper/get', 'GET')).toBeGreaterThanOrEqual(2);
    expect(
      mockApi.requests
        .filter((request) => request.pathname === '/api/paper/get')
        .every((request) => request.search.includes('id=paper-lifecycle')),
    ).toBe(true);
    await audit.assertClean({ requireWorker: true });
  });

  test('translates a real PDF text-layer selection through the fixed paper request', async ({
    mockApi,
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.goto('/workspace/reader/paper-lifecycle');
    await expect(page.getByRole('article', { name: '第 1 页' })).toHaveAttribute('data-status', 'ready');

    await selectFirstPdfText(page);
    const translation = page.getByRole('dialog', { name: '选文翻译' });
    await expect(translation).toBeVisible();
    await translation.getByRole('button', { name: '翻译选文' }).click();
    await expect(translation.getByText(/译文：Paper Study deterministic PDF fixture/)).toBeVisible();

    expect(mockApi.lastRequest('/api/translate-text', 'POST')?.body).toEqual({
      text: 'Paper Study deterministic PDF fixture',
    });
    await translation.getByRole('button', { name: '关闭选文翻译' }).click();
    await expect(translation).toHaveCount(0);
    await audit.assertClean({ requireWorker: true });
  });

  test('saves notes and generates artifacts while preserving keyboard tab semantics', async ({
    mockApi,
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.goto('/workspace/reader/paper-lifecycle');

    const artifacts = page.getByRole('region', { name: '研究产物' });
    const note = artifacts.getByRole('textbox', { name: '笔记内容' });
    await note.fill('# E2E 阅读笔记\n\n只提交当前论文。');
    await artifacts.getByRole('button', { name: '保存笔记' }).click();
    await expect(artifacts.getByRole('status').filter({ hasText: '笔记已保存' })).toBeVisible();
    expect(mockApi.lastRequest('/api/note', 'POST')?.body).toEqual({
      id: 'paper-lifecycle',
      content: '# E2E 阅读笔记\n\n只提交当前论文。',
    });

    const noteTab = artifacts.getByRole('tab', { name: '笔记' });
    await noteTab.focus();
    await page.keyboard.press('ArrowRight');
    const explainerTab = artifacts.getByRole('tab', { name: '讲解' });
    await expect(explainerTab).toBeFocused();
    await expect(explainerTab).toHaveAttribute('aria-selected', 'true');
    await artifacts.getByRole('button', { name: '生成讲解' }).click();
    await expect(artifacts.getByRole('status').filter({ hasText: '讲解已完成' })).toBeVisible();
    expect(mockApi.lastRequest('/api/explain', 'POST')?.body).toEqual({
      id: 'paper-lifecycle',
      deep: false,
    });
    await audit.assertClean();
  });

  test('reports missing papers and no-PDF papers as distinct recoverable states', async ({
    page,
  }) => {
    const audit = await installRuntimeAudit(page);
    await page.goto('/workspace/reader/paper-evidence');
    await expect(page.getByRole('heading', { name: '证据优先的文献综述界面' })).toBeVisible();
    await expect(page.getByRole('heading', { name: '尚未关联 PDF' })).toBeVisible();
    await expect(page.getByRole('link', { name: '前往本地 PDF 补齐' })).toHaveAttribute(
      'href',
      '/workspace/acquire',
    );

    await page.goto('/workspace/reader/not-in-fixture');
    await expect(page.getByRole('heading', { name: '找不到这篇论文' })).toBeVisible();
    await expect(page.getByRole('link', { name: '返回论文库' })).toBeVisible();
    await audit.assertClean();
  });
});
