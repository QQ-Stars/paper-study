const assert = require('node:assert/strict');
const fs = require('node:fs');
const test = require('node:test');

const { createIngestRenderer } = require('../public/ingest-rendering');

test('ingest UI delegates untrusted rendering to the safe renderer', () => {
  const root = require('node:path').resolve(__dirname, '..');
  const index = fs.readFileSync(require('node:path').join(root, 'public', 'index.html'), 'utf8');
  const app = fs.readFileSync(require('node:path').join(root, 'public', 'app.js'), 'utf8');
  const functionBody = (name) => {
    const match = app.match(new RegExp(`function\\s+${name}\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n\\}`, 'm'));
    assert.ok(match, `expected ${name} function`);
    return match[0];
  };

  assert.match(index, /<script\s+src=["']ingest-rendering\.js["']><\/script>\s*<script\s+src=["']app\.js["']><\/script>/);
  assert.match(app, /let\s+candidates\s*=\s*\[\]\s*;\s*const\s+ingestRenderer\s*=\s*window\.IngestRendering\.createIngestRenderer\(\{\s*document\s*}\)\s*;/);

  const chips = functionBody('renderQueryChips');
  assert.match(chips, /ingestRenderer\.renderQueryChips\(\s*\$\(['"]#ingQueryChips['"]\)\s*,\s*qs\s*,/);
  assert.doesNotMatch(chips, /box\.innerHTML\s*=\s*qs\.map/);

  const detail = functionBody('setDetail');
  assert.match(detail, /ingestRenderer\.setDetail\(\s*\$\(['"]#ingdMain['"]\)\s*,\s*\$\(['"]#ingdSub['"]\)\s*,\s*main\s*,\s*sub\s*,\s*warning\s*\)/);
  assert.doesNotMatch(detail, /\$\(['"]#ingdSub['"]\)\.innerHTML\s*=\s*sub/);

  const progress = functionBody('handleProgress');
  const sourceError = progress.match(/else\s+if\s*\(line\.startsWith\(['"]SRCERR::['"]\)\)\s*\{([\s\S]*?)\n\s*}\s*else\s+if/);
  assert.ok(sourceError, 'expected SRCERR branch');
  assert.match(sourceError[1], /setDetail\(\s*null\s*,[\s\S]*?,\s*true\s*\)/);
  assert.doesNotMatch(sourceError[1], /<b\b/);
  const doing = progress.match(/else\s+if\s*\(line\.startsWith\(['"]DOING::['"]\)\)\s*\{([\s\S]*?)\n\s*}\s*else\s+if/);
  assert.ok(doing, 'expected DOING branch');
  assert.match(doing[1], /setDetail\([\s\S]*?`《\$\{title\}》`/);
  assert.doesNotMatch(doing[1], /esc\(title\)/);

  const candidateList = functionBody('renderCandidates');
  assert.match(candidateList, /const\s+box\s*=\s*\$\(['"]#candList['"]\)\s*;\s*box\.replaceChildren\(\)\s*;/);
  assert.match(candidateList, /ingestRenderer\.createCandidateCard\(\s*\{[\s\S]*?candidate[\s\S]*?index[\s\S]*?venueName[\s\S]*?sourceLabels\s*:\s*SRC_SHORT[\s\S]*?}\s*\)/);
  assert.doesNotMatch(candidateList, /<div\s+class=["']cand-title["']>\$\{c\.title}/);
});

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

class FakeTextNode {
  constructor(text) {
    this.children = [];
    this.className = '';
    this.textContent = text;
  }
}

function fakeDocument() {
  return {
    createElement(tagName) {
      return new FakeElement(tagName);
    },
    createTextNode(text) {
      return new FakeTextNode(text);
    },
  };
}

function findByClass(node, className) {
  if (node.className.split(/\s+/).includes(className)) return node;
  for (const child of node.children) {
    const found = findByClass(child, className);
    if (found) return found;
  }
  return undefined;
}

function deepTextContent(node) {
  return node.textContent + node.children.map(deepTextContent).join('');
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

test('createCandidateCard renders untrusted candidate fields as literal text', () => {
  const renderer = createIngestRenderer({ document: fakeDocument() });
  const title = '<img src=x onerror=alert(1)>';
  const venueName = 'CVPR\"><svg onload=alert(2)>';
  const year = '<img src=x onerror=alert(4)>';
  const type = '<b>type</b>';
  const topic = '<i>topic</i>';
  const note = '<svg onload=alert(3)>';

  const card = renderer.createCandidateCard({
    candidate: {
      title,
      year,
      type,
      topic,
      _verify: { skipped: true, note, source_of_truth: 'dblp' },
      in_library: false,
      ccf: 'A',
      relevance: 0.73,
    },
    index: 4,
    venueName,
    sourceLabels: { dblp: 'DBLP' },
  });

  assert.equal(card.tagName, 'LABEL');
  assert.ok(card.className.split(/\s+/).includes('cand'));
  const checkbox = card.children[0];
  assert.equal(checkbox.className, 'cand-ck');
  assert.equal(checkbox.type, 'checkbox');
  assert.equal(checkbox.dataset.i, '4');
  assert.equal(checkbox.checked, true);
  assert.equal(checkbox.disabled, false);

  const titleNode = findByClass(card, 'cand-title');
  assert.equal(titleNode.textContent, title);
  assert.equal(titleNode.children.length, 0);
  const venue = findByClass(card, 'venue');
  assert.equal(venue.textContent, `${venueName} ${year}`);
  assert.equal(venue.children.length, 0);
  const venueClasses = venue.className.split(/\s+/);
  assert.equal(venueClasses.length, 2);
  assert.match(venueClasses[1], /^v-[A-Za-z0-9_-]+$/);
  const verification = findByClass(card, 'vbadge');
  assert.ok(verification.className.split(/\s+/).includes('src'));
  assert.equal(verification.title, note);
  assert.equal(verification.textContent, '源自DBLP');
  assert.equal(verification.children.length, 0);
  const meta = findByClass(card, 'cand-meta');
  const verificationIndex = meta.children.indexOf(verification);
  assert.equal(meta.children[verificationIndex - 1].textContent, ' ');
  assert.equal(meta.children[verificationIndex - 1].children.length, 0);
  assert.ok(meta.children.every((child) => child.textContent !== title && child.textContent !== type && child.textContent !== topic && child.textContent !== note));
  assert.match(deepTextContent(meta), new RegExp(type.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  assert.match(deepTextContent(meta), new RegExp(topic.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  const typeLeaf = meta.children.find((child) => child.textContent === ` · ${type}`);
  const topicLeaf = meta.children.find((child) => child.textContent === ` · ${topic}`);
  assert.equal(typeLeaf.children.length, 0);
  assert.equal(topicLeaf.children.length, 0);
  assert.ok(verificationIndex < meta.children.indexOf(typeLeaf));
  assert.ok(verificationIndex < meta.children.indexOf(topicLeaf));
  const bar = findByClass(card, 'cand-rel-bar');
  assert.equal(bar.style.width, '73%');
  const relevance = findByClass(card, 'cand-rel');
  assert.equal(relevance.title, '相关度 73%');
  assert.equal(relevance.children[1].textContent, '73');
});

test('createCandidateCard clamps and rounds relevance without a visible percent suffix', () => {
  const renderer = createIngestRenderer({ document: fakeDocument() });
  const cases = [
    [NaN, 0],
    [Infinity, 0],
    ['not a number', 0],
    [-0.2, 0],
    [1.2, 100],
    [0.736, 74],
  ];

  for (const [input, expected] of cases) {
    const card = renderer.createCandidateCard({
      candidate: { title: 'Safe title', relevance: input },
      index: 0,
      venueName: 'Safe venue',
      sourceLabels: {},
    });
    const relevance = findByClass(card, 'cand-rel');
    assert.equal(relevance.children[1].textContent, String(expected));
    assert.equal(relevance.title, `相关度 ${expected}%`);
    assert.equal(findByClass(card, 'cand-rel-bar').style.width, `${expected}%`);
  }
});

test('createCandidateCard retains the empty type separator before a topic', () => {
  const renderer = createIngestRenderer({ document: fakeDocument() });
  const topic = '<i>topic</i>';
  const card = renderer.createCandidateCard({
    candidate: { title: 'Safe title', type: '', topic },
    index: 0,
    venueName: 'Safe venue',
    sourceLabels: {},
  });

  const meta = findByClass(card, 'cand-meta');
  const typeSeparatorIndex = meta.children.findIndex((child) => child.textContent === ' · ');
  const topicLeaf = meta.children.find((child) => child.textContent === ` · ${topic}`);
  assert.notEqual(typeSeparatorIndex, -1);
  assert.equal(meta.children[typeSeparatorIndex].children.length, 0);
  assert.equal(topicLeaf.children.length, 0);
  assert.ok(typeSeparatorIndex < meta.children.indexOf(topicLeaf));
  assert.ok(deepTextContent(meta).includes(` ·  · ${topic}`));
});

test('createCandidateCard preserves in-library checkbox selection behavior', () => {
  const renderer = createIngestRenderer({ document: fakeDocument() });

  const card = renderer.createCandidateCard({
    candidate: {
      title: 'Safe title',
      year: 2025,
      in_library: true,
    },
    index: 0,
    venueName: 'NeurIPS',
    sourceLabels: {},
  });

  assert.ok(card.className.split(/\s+/).includes('in-lib'));
  assert.equal(card.children[0].checked, false);
  assert.equal(card.children[0].disabled, true);
  const meta = findByClass(card, 'cand-meta');
  const inLibraryTag = findByClass(card, 'inlib-tag');
  const inLibraryIndex = meta.children.indexOf(inLibraryTag);
  assert.equal(meta.children[inLibraryIndex - 1].textContent, ' · ');
  assert.equal(meta.children[inLibraryIndex - 1].children.length, 0);
  assert.equal(inLibraryTag.textContent, '已在库');
});

test('createCandidateCard renders an unknown hostile verification source literally', () => {
  const renderer = createIngestRenderer({ document: fakeDocument() });
  const source = '<img src=x onerror=alert(1)>';

  const card = renderer.createCandidateCard({
    candidate: {
      title: 'Safe title',
      _verify: { skipped: true, source_of_truth: source },
    },
    index: 0,
    venueName: 'Safe venue',
    sourceLabels: Object.create({ [source]: 'not an own label' }),
  });

  const verification = findByClass(card, 'vbadge');
  assert.equal(verification.textContent, `源自${source}`);
  assert.equal(verification.children.length, 0);
});
