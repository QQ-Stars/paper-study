import test from 'node:test';
import assert from 'node:assert/strict';

import {
  PDF_DEFAULT_SCALE,
  clampPdfPage,
  clampPdfScale,
  parseSavedPdfPosition,
  pdfRenderWindow,
} from '../src/components/pdfViewerState.ts';

test('saved PDF positions are validated and bounded', () => {
  assert.deepEqual(parseSavedPdfPosition('{"page":4,"scale":1.4}'), { page: 4, scale: 1.4 });
  assert.deepEqual(parseSavedPdfPosition('{"page":3.6,"scale":9}'), { page: 4, scale: 2.2 });
  assert.equal(parseSavedPdfPosition('{"page":"4","scale":1}'), null);
  assert.equal(parseSavedPdfPosition('not-json'), null);
  assert.equal(clampPdfScale(Number.NaN), PDF_DEFAULT_SCALE);
  assert.equal(clampPdfPage(99, 9), 9);
});

test('continuous PDF rendering keeps only nearby canvases active', () => {
  assert.deepEqual(pdfRenderWindow(1, 9), [1, 2, 3]);
  assert.deepEqual(pdfRenderWindow(5, 9), [3, 4, 5, 6, 7]);
  assert.deepEqual(pdfRenderWindow(9, 9), [7, 8, 9]);
  assert.deepEqual(pdfRenderWindow(4, 9, 0), [4]);
  assert.deepEqual(pdfRenderWindow(1, 0), []);
});
