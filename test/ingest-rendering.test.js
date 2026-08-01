const assert = require('node:assert/strict');
const test = require('node:test');

const { createIngestRenderer } = require('../public/ingest-rendering');

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

test('setDetail writes hostile progress text literally and applies warning styling', () => {
  const main = new FakeElement('div');
  const sub = new FakeElement('div');
  const renderer = createIngestRenderer({ document: fakeDocument() });

  renderer.setDetail(main, sub, '<svg onload=alert(1)>', '<b>source failed</b>', true);

  assert.equal(main.textContent, '<svg onload=alert(1)>');
  assert.equal(sub.textContent, '<b>source failed</b>');
  assert.equal(sub.className, 'ingd-sub ingd-warn');
});
