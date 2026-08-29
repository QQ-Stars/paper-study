import assert from 'node:assert/strict';
import test from 'node:test';

import {
  LONG_DOCUMENT_FORMULA_TARGET,
  LONG_DOCUMENT_PAGE_TARGET,
  paginateMarkdown,
} from '../src/components/longDocument.ts';

test('short markdown remains a single continuous page', () => {
  const source = '# 标题\n\n这是一段适合连续阅读的短文。';
  assert.deepEqual(paginateMarkdown(source), [source]);
});

test('long markdown is split into bounded pages without losing content', () => {
  const section = (index) =>
    `## 第 ${index} 节\n\n这是用于验证长文分页的正文段落 ${index}。\n\n- 结论 A\n- 结论 B\n\n`;
  const source = Array.from({ length: 900 }, (_, index) => section(index + 1)).join('');
  const pages = paginateMarkdown(source);

  assert.ok(pages.length > 2);
  assert.equal(pages.join(''), source);
  assert.ok(
    pages.every((page) => page.length <= LONG_DOCUMENT_PAGE_TARGET * 1.5),
    `page sizes: ${pages.map((page) => page.length).join(', ')}`,
  );
});

test('pagination never cuts through fenced code or block math', () => {
  const prefix = '普通段落。\n\n'.repeat(1500);
  const code = '```python\n' + 'print("keep together")\n'.repeat(900) + '```\n\n';
  const math = '$$\n' + 'x_i = W_q h_i \\\\tag{1}\n'.repeat(900) + '$$\n\n';
  const suffix = '结尾段落。\n\n'.repeat(1500);
  const pages = paginateMarkdown(prefix + code + math + suffix);

  assert.equal(pages.join(''), prefix + code + math + suffix);
  const codePage = pages.find((page) => page.includes('```python'));
  const mathPage = pages.find((page) => page.includes('\\tag{1}'));
  assert.ok(codePage);
  assert.ok(mathPage);
  assert.equal(codePage.match(/print\("keep together"\)/g)?.length, 900);
  assert.equal(mathPage.match(/\\tag\{1\}/g)?.length, 900);
  assert.match(codePage, /```python[\s\S]*```\r?\n?$/);
  assert.match(mathPage, /\$\$[\s\S]*\$\$\r?\n?$/);
});

test('formula-dense markdown paginates before the character threshold', () => {
  const source = Array.from(
    { length: LONG_DOCUMENT_FORMULA_TARGET + 30 },
    (_, index) => `公式段落 ${index + 1}：$x_${index}=W_qh_${index}$。\n\n`,
  ).join('');
  const pages = paginateMarkdown(source);

  assert.ok(source.length < 20_000);
  assert.ok(pages.length > 1);
  assert.equal(pages.join(''), source);
});
