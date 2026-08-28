import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const acquirePageSource = readFileSync(
  new URL('../src/components/AcquirePage.tsx', import.meta.url),
  'utf8',
);

test('expand request exposes a visible busy state until the request settles', () => {
  assert.match(
    acquirePageSource,
    /const \[expanding, setExpanding\] = useState\(false\)/,
    'the expand request needs its own loading state',
  );
  assert.match(
    acquirePageSource,
    /setExpanding\(true\)[\s\S]*?await acquireApi\.expand[\s\S]*?finally\s*{\s*setExpanding\(false\)/,
    'loading must start before the request and always reset in finally',
  );
  assert.match(
    acquirePageSource,
    /aria-busy={expanding}/,
    'assistive technology needs the button busy state',
  );
  assert.match(
    acquirePageSource,
    /disabled={expanding[^}]*}/,
    'the button must reject duplicate requests while expanding',
  );
  assert.match(
    acquirePageSource,
    /expanding\s*\?\s*'扩展中…'\s*:\s*'扩展检索词'/,
    'the button label must visibly change during the request',
  );
  assert.match(
    acquirePageSource,
    /role="status"[\s\S]*?aria-live="polite"[\s\S]*?正在生成扩展检索词/,
    'the page needs a persistent live status message while waiting',
  );
});

test('expanding terms never writes a replacement value into the controlled query input', () => {
  const expandBlock = acquirePageSource.slice(
    acquirePageSource.indexOf('const runExpand'),
    acquirePageSource.indexOf('const runSearch'),
  );
  assert.doesNotMatch(
    expandBlock,
    /setQuery\(/,
    'the expansion response must not replace the user\'s query text',
  );
  assert.match(
    acquirePageSource,
    /onChange=\{\(event\) => updateQuery\(event\.target\.value\)\}/,
    'query edits must go through the state/expanded-term synchronizer',
  );
});
