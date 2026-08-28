import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const settingsSource = readFileSync(
  new URL('../src/components/SettingsPage.tsx', import.meta.url),
  'utf8',
);
const settingsStyles = readFileSync(
  new URL('../src/styles/pages4.css', import.meta.url),
  'utf8',
);

test('the reproduction directory uses the same neutral row treatment as other data directories', () => {
  assert.doesNotMatch(
    settingsSource,
    /className="settings-row--reproduction"/,
    'the reproduction directory must not be rendered as a separate warning card',
  );
  assert.doesNotMatch(
    settingsStyles,
    /\.settings-row--reproduction\b/,
    'the one-off reproduction row style should not remain in the design system',
  );
});

test('directory restart guidance is shared by the section instead of repeated in one row', () => {
  assert.match(
    settingsSource,
    /title="数据目录"\s+desc="[^"]*修改后需重启后端生效[^"]*"/,
  );
  assert.match(
    settingsSource,
    /title="论文复现目录"\s+desc={`当前：\$\{settings\.resolvedReproductionDir[^}]*\}，留空＝默认 data\/reproduction-artifacts`}/,
  );
});
