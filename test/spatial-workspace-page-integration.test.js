const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const publicDir = path.resolve(__dirname, '..', 'public');
const html = fs.readFileSync(path.join(publicDir, 'index.html'), 'utf8');
const style = fs.readFileSync(path.join(publicDir, 'style.css'), 'utf8');

test('classic mode hides one semantic spatial overview while preserving homeTable', () => {
  assert.equal((html.match(/id="spatialOverview"/g) || []).length, 1);
  assert.equal((html.match(/id="homeTable"/g) || []).length, 1);
  assert.match(style, /\.spatial-only\s*\{\s*display:\s*none\s*;?\s*\}/);
});

test('overview exposes queue, layers, inspector and explicit paths', () => {
  for (const id of [
    'spatialQueue', 'spatialLayers', 'spatialEmpty', 'spatialInspector',
    'spatialPrev', 'spatialNext', 'spatialOpen', 'spatialClearFilters',
    'spatialContext', 'spatialDirections', 'spatialQueueClose', 'spatialInspectorClose',
    'spatialFilterSlot', 'homeFilterActions',
  ]) assert.match(html, new RegExp(`id="${id}"`));
  assert.match(html, /href="#homeTable"[^>]*>查看完整数据</);
});

test('the single real home filter group stays intact beside the direct search child', () => {
  for (const id of ['homeFilterActions', 'yearFilters', 'semToggle', 'favFilter']) {
    assert.equal((html.match(new RegExp(`id="${id}"`, 'g')) || []).length, 1, `${id} must stay unique`);
  }
  assert.match(
    html,
    /<div\b(?=[^>]*\bid="topFilters")(?=[^>]*\bclass="filters")[^>]*>\s*<div\b[^>]*class="search-box"/,
  );
  assert.match(
    html,
    /<div\b[^>]*id="homeFilterActions"[^>]*>\s*<div\b[^>]*id="yearFilters"[^>]*><\/div>\s*<button\b[^>]*id="semToggle"[^>]*>[\s\S]*?<\/button>\s*<button\b[^>]*id="favFilter"[^>]*>[\s\S]*?<\/button>\s*<\/div>/,
  );
});

test('spatial-workspace loads once before the protected markdown script trio', () => {
  const sources = [...html.matchAll(/<script\b[^>]*src="([^"]+)"[^>]*>/g)].map(match => path.basename(match[1]));
  assert.equal(sources.filter(source => source === 'spatial-workspace.js').length, 1);
  const spatialIndex = sources.indexOf('spatial-workspace.js');
  assert.deepEqual(sources.slice(spatialIndex + 1, spatialIndex + 4), [
    'ingest-rendering.js',
    'markdown-rendering-coordinator.js',
    'app.js',
  ]);
});
