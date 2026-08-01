const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const { createIngestRenderer } = require('../public/ingest-rendering');

test('iq-x button rule resets native button styling', () => {
  const css = fs.readFileSync(require.resolve('../public/style.css'), 'utf8');
  const rule = css.match(/\.iq-x\s*\{([^}]*)\}/);

  assert.ok(rule, 'expected an .iq-x CSS rule');
  for (const property of [
    /border\s*:\s*0\b/,
    /background\s*:\s*transparent\b/,
    /padding\s*:\s*0\b/,
    /appearance\s*:\s*none\b/,
    /font\s*:\s*inherit\b/,
  ]) {
    assert.match(rule[1], property);
  }
});

class FakeElement {
  constructor(tagName) {
    this.tagName = tagName.toUpperCase();
    this.children = [];
    this.dataset = {};
    this.className = '';
    this.textContent = '';
    this.title = '';
    this.type = '';
    this.disabled = false;
    this.checked = false;
    this.style = {};
    this.listeners = {};
  }

  append(...nodes) {
    this.children.push(...nodes);
  }

  replaceChildren(...nodes) {
    this.children = nodes;
    this.textContent = '';
  }

  addEventListener(type, listener) {
    this.listeners[type] = listener;
  }

  click() {
    if (this.listeners.click) this.listeners.click();
  }
}

function fakeDocument() {
  return {
    createElement(tagName) {
      return new FakeElement(tagName);
    },
  };
}

test('renderQueryChips renders hostile query text as a text-only span', () => {
  const box = new FakeElement('div');
  let removedIndex;
  const renderer = createIngestRenderer({ document: fakeDocument() });
  const hostileQuery = '<img src=x onerror=alert(1)>';

  renderer.renderQueryChips(box, [hostileQuery], (index) => {
    removedIndex = index;
  });

  assert.equal(box.dataset.qs, JSON.stringify([hostileQuery]));
  assert.equal(box.children.length, 1);
  const chip = box.children[0];
  assert.equal(chip.className, 'iq-chip');
  const queryText = chip.children[0];
  assert.equal(queryText.tagName, 'SPAN');
  assert.equal(queryText.textContent, hostileQuery);
  assert.equal(queryText.children.length, 0);
  const removeButton = chip.children[1];
  assert.equal(removeButton.className, 'iq-x');
  assert.equal(removeButton.type, 'button');
  assert.equal(removeButton.dataset.i, '0');
  removeButton.click();
  assert.equal(removedIndex, 0);
});

test('renderQueryChips renders the placeholder for a non-array query value', () => {
  const box = new FakeElement('div');
  const renderer = createIngestRenderer({ document: fakeDocument() });

  renderer.renderQueryChips(box, null, () => {});

  assert.equal(box.dataset.qs, '[]');
  assert.equal(box.children.length, 1);
  const placeholder = box.children[0];
  assert.equal(placeholder.tagName, 'SPAN');
  assert.equal(placeholder.className, 'placeholder');
  assert.equal(placeholder.textContent, '（无检索词）');
});

test('renderQueryChips reports the clicked index for multiple literal query labels', () => {
  const box = new FakeElement('div');
  let removedIndex;
  const renderer = createIngestRenderer({ document: fakeDocument() });
  const queries = ['<img src=x onerror=alert(1)>', 'plain query'];

  renderer.renderQueryChips(box, queries, (index) => {
    removedIndex = index;
  });

  assert.equal(box.children[0].children[0].textContent, queries[0]);
  assert.equal(box.children[0].children[0].children.length, 0);
  assert.equal(box.children[1].children[0].textContent, queries[1]);
  assert.equal(box.children[1].children[0].children.length, 0);
  box.children[1].children[1].click();
  assert.equal(removedIndex, 1);
});

test('setDetail writes hostile progress text literally and applies warning styling', () => {
  const main = new FakeElement('div');
  const sub = new FakeElement('div');
  const renderer = createIngestRenderer({ document: fakeDocument() });

  renderer.setDetail(main, sub, '<svg onload=alert(1)>', '<b>source failed</b>', true);

  assert.equal(main.textContent, '<svg onload=alert(1)>');
  assert.equal(sub.textContent, '<b>source failed</b>');
  assert.equal(sub.className, 'ingd-sub ingd-warn');
});
