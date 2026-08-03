const assert = require('node:assert/strict');
const path = require('node:path');
const test = require('node:test');

const workspacePath = path.resolve(__dirname, '..', 'public', 'spatial-workspace.js');
const papers = Array.from({ length: 8 }, (_, index) => ({
  id: `p${index + 1}`,
  title: `Paper ${index + 1}`,
  venue: 'CVPR',
  year: '2026',
  status: index % 2 ? '学习中' : '未开始',
}));
const stringNullishPapers = [
  { id: 'first' },
  { id: 'null' },
  { id: 'undefined' },
];

test('empty results have no selection and no fake layers', () => {
  const { createWorkspaceState } = require(workspacePath);
  const state = createWorkspaceState([], 'missing');
  assert.deepEqual(state.visiblePapers, []);
  assert.equal(state.selectedId, null);
  assert.equal(state.total, 0);
  assert.equal(state.canPrevious, false);
  assert.equal(state.canNext, false);
});

test('initial state prefers the current paper when it exists', () => {
  const { createWorkspaceState } = require(workspacePath);
  const state = createWorkspaceState(papers, 'p4');
  assert.equal(state.selectedId, 'p4');
  assert.equal(state.selectedIndex, 3);
});

test('an unavailable preferred paper falls back to the first result', () => {
  const { createWorkspaceState } = require(workspacePath);
  assert.equal(createWorkspaceState(papers, 'missing').selectedId, 'p1');
});

test('one result produces exactly one layer and disabled boundaries', () => {
  const { createWorkspaceState } = require(workspacePath);
  const state = createWorkspaceState(papers.slice(0, 1));
  assert.deepEqual(state.visiblePapers.map(paper => paper.id), ['p1']);
  assert.equal(state.canPrevious, false);
  assert.equal(state.canNext, false);
});

test('many results expose at most five real adjacent papers and the exact total', () => {
  const { createWorkspaceState } = require(workspacePath);
  const state = createWorkspaceState(papers, 'p5');
  assert.equal(state.total, 8);
  assert.deepEqual(state.visiblePapers.map(paper => paper.id), ['p3', 'p4', 'p5', 'p6', 'p7']);
});

test('selecting another paper already in the layer window preserves that window', () => {
  const { createWorkspaceState, selectPaper } = require(workspacePath);
  const state = createWorkspaceState(papers, 'p4');
  const next = selectPaper(state, 'p5');
  assert.equal(next.visibleStart, state.visibleStart);
  assert.deepEqual(next.visiblePapers.map(paper => paper.id), state.visiblePapers.map(paper => paper.id));
});

test('filter and sort reconciliation preserves a valid selection', () => {
  const { createWorkspaceState, reconcilePapers } = require(workspacePath);
  const state = createWorkspaceState(papers, 'p5');
  const next = reconcilePapers(state, [papers[6], papers[4], papers[2]]);
  assert.equal(next.selectedId, 'p5');
  assert.equal(next.selectedIndex, 1);
});

test('filter reconciliation selects the first result after invalidation', () => {
  const { createWorkspaceState, reconcilePapers } = require(workspacePath);
  const state = createWorkspaceState(papers, 'p5');
  assert.equal(reconcilePapers(state, [papers[7], papers[6]]).selectedId, 'p8');
});

test('previous and next never wrap', () => {
  const { createWorkspaceState, moveSelection } = require(workspacePath);
  const first = createWorkspaceState(papers.slice(0, 3), 'p1');
  assert.equal(moveSelection(first, -1).selectedId, 'p1');
  const last = createWorkspaceState(papers.slice(0, 3), 'p3');
  assert.equal(moveSelection(last, 1).selectedId, 'p3');
});

test('nullish preferred ids do not collide with string paper ids', () => {
  const { createWorkspaceState } = require(workspacePath);
  assert.equal(createWorkspaceState(stringNullishPapers).selectedId, 'first');
  assert.equal(createWorkspaceState(stringNullishPapers, null).selectedId, 'first');
  assert.equal(createWorkspaceState(stringNullishPapers, undefined).selectedId, 'first');
});

test('an explicit string null id remains selectable', () => {
  const { createWorkspaceState, selectPaper } = require(workspacePath);
  assert.equal(createWorkspaceState(stringNullishPapers, 'null').selectedId, 'null');
  const state = createWorkspaceState(stringNullishPapers, 'first');
  assert.equal(selectPaper(state, 'null').selectedId, 'null');
});

test('reconciliation preserves an explicit string null selection', () => {
  const { createWorkspaceState, reconcilePapers } = require(workspacePath);
  const state = createWorkspaceState(stringNullishPapers, 'null');
  const next = reconcilePapers(state, [stringNullishPapers[2], stringNullishPapers[1]]);
  assert.equal(next.selectedId, 'null');
  assert.equal(next.selectedIndex, 1);
});

test('nullish direct selections are no-ops', () => {
  const { createWorkspaceState, selectPaper } = require(workspacePath);
  const state = createWorkspaceState(stringNullishPapers, 'first');
  assert.equal(selectPaper(state, null), state);
  assert.equal(selectPaper(state, undefined), state);
});
