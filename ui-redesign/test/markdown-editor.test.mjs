import assert from 'node:assert/strict';
import test from 'node:test';

import {
  applyMarkdownCommand,
  commandForMarkdownShortcut,
  continueMarkdownList,
  indentMarkdown,
  normalizedImageFilename,
  supportedImageMimeType,
} from '../src/components/markdownEditor.ts';

test('markdown commands wrap inline text and keep the inner selection', () => {
  const result = applyMarkdownCommand('记录结果', { start: 0, end: 4 }, 'bold');
  assert.equal(result.value, '**记录结果**');
  assert.equal(result.value.slice(result.selectionStart, result.selectionEnd), '记录结果');
});

test('table command inserts a three-column template and focuses the first body cell', () => {
  const result = applyMarkdownCommand('', { start: 0, end: 0 }, 'table');
  assert.match(result.value, /\| 列 1 \| 列 2 \| 列 3 \|/);
  assert.match(result.value, /\| --- \| --- \| --- \|/);
  assert.equal(result.value.slice(result.selectionStart, result.selectionEnd), '内容');
});

test('line commands toggle lists and Enter continues numbered lists', () => {
  const listed = applyMarkdownCommand('实验结果', { start: 0, end: 4 }, 'unordered-list');
  assert.equal(listed.value, '- 实验结果');
  const toggled = applyMarkdownCommand(listed.value, { start: 0, end: listed.value.length }, 'unordered-list');
  assert.equal(toggled.value, '实验结果');

  const continued = continueMarkdownList('1. 第一个', 6);
  assert.equal(continued?.value, '1. 第一个\n2. ');
  assert.equal(continued?.selectionStart, continued?.value.length);
});

test('multiline code selections become fenced code blocks', () => {
  const source = 'python\nprint(1)';
  const result = applyMarkdownCommand(source, { start: 0, end: source.length }, 'code');
  assert.equal(result.value, '```\npython\nprint(1)\n```');
});

test('link command selects the full URL placeholder for quick replacement', () => {
  const result = applyMarkdownCommand('结果', { start: 0, end: 2 }, 'link');
  assert.equal(result.value.slice(result.selectionStart, result.selectionEnd), 'https://');
});

test('Tab and Shift+Tab indent and outdent markdown blocks', () => {
  const indented = indentMarkdown('第一行\n第二行', { start: 0, end: 8 }, 'indent');
  assert.equal(indented.value, '  第一行\n  第二行');
  const outdented = indentMarkdown(indented.value, { start: 0, end: indented.value.length }, 'outdent');
  assert.equal(outdented.value, '第一行\n第二行');
});

test('common editor shortcuts map to markdown commands', () => {
  assert.equal(commandForMarkdownShortcut({ key: 'b', ctrlKey: true }), 'bold');
  assert.equal(commandForMarkdownShortcut({ key: 'i', metaKey: true }), 'italic');
  assert.equal(commandForMarkdownShortcut({ key: 'x', ctrlKey: true, shiftKey: true }), 'strike');
  assert.equal(commandForMarkdownShortcut({ key: '`', code: 'Backquote', ctrlKey: true }), 'code');
  assert.equal(commandForMarkdownShortcut({ key: 't', ctrlKey: true, altKey: true }), 'table');
  assert.equal(commandForMarkdownShortcut({ key: '&', code: 'Digit7', ctrlKey: true, shiftKey: true }), 'ordered-list');
  assert.equal(commandForMarkdownShortcut({ key: '*', code: 'Digit8', ctrlKey: true, shiftKey: true }), 'unordered-list');
  assert.equal(commandForMarkdownShortcut({ key: '7', ctrlKey: true, shiftKey: true }), 'ordered-list');
  assert.equal(commandForMarkdownShortcut({ key: 'b', ctrlKey: true, altKey: true }), null);
});

test('image insertion accepts server-supported formats and normalizes pasted names', () => {
  assert.equal(supportedImageMimeType({ type: 'image/png', name: 'figure.png' }), 'image/png');
  assert.equal(supportedImageMimeType({ type: '', name: 'clipboard.jpeg' }), 'image/jpeg');
  assert.equal(supportedImageMimeType({ type: 'image/jpg', name: 'figure.jpg' }), 'image/jpeg');
  assert.equal(supportedImageMimeType({ type: 'image/gif', name: 'animation.gif' }), null);
  assert.equal(normalizedImageFilename({ type: 'image/jpeg', name: 'clipboard' }, 'image/jpeg'), 'clipboard.jpg');
  assert.equal(normalizedImageFilename({ type: 'image/png', name: '' }, 'image/png'), 'pasted-image.png');
  assert.ok(normalizedImageFilename({ type: 'image/png', name: 'x'.repeat(300) }, 'image/png').length <= 255);
});
