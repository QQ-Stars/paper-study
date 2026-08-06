const assert = require('node:assert/strict');
const test = require('node:test');

const { buildImportPdfsTerminal } = require('../lib/import-pdfs-result');

test('local PDF import terminal preserves the importer total and counters', () => {
  assert.deepEqual(
    buildImportPdfsTerminal(0, JSON.stringify({
      ok: true,
      total: 9,
      added: 3,
      dup: 2,
      failed: 2,
    })),
    {
      type: 'result',
      ok: true,
      total: 9,
      added: 3,
      dup: 2,
      failed: 2,
      error: '',
    },
  );
});

test('local PDF import terminal remains complete for failed and malformed results', () => {
  assert.deepEqual(
    buildImportPdfsTerminal(1, JSON.stringify({
      ok: false,
      total: 4,
      added: 1,
      failed: 3,
      error: 'classification failed',
    })),
    {
      type: 'result',
      ok: false,
      total: 4,
      added: 1,
      dup: 0,
      failed: 3,
      error: 'classification failed',
    },
  );

  assert.deepEqual(buildImportPdfsTerminal(0, 'not json'), {
    type: 'result',
    ok: false,
    total: 0,
    added: 0,
    dup: 0,
    failed: 0,
    error: '导入结果格式无效',
  });
});
