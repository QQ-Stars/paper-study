const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  writeFileSync,
} = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

const REPO_ROOT = path.resolve(__dirname, '..');
const CLI_PATH = path.join(REPO_ROOT, 'scripts', 'pre-existing-failure-baseline.mjs');
const FULL_SUITE_COMMAND = 'npm.cmd run test:run --prefix frontend';

function candidate(overrides = {}) {
  return {
    version: 'pre-existing-test-failures-v1',
    command: FULL_SUITE_COMMAND,
    exitCode: 0,
    failedTestIds: [],
    normalizedStackSignatures: [],
    relatedFileSha256: [],
    capturedAt: '2026-08-09T00:00:00.000Z',
    sourceTreeHash: 'a'.repeat(64),
    ...overrides,
  };
}

function runCli(args, options = {}) {
  return spawnSync(process.execPath, [options.cliPath ?? CLI_PATH, ...args], {
    cwd: options.cwd ?? REPO_ROOT,
    encoding: 'utf8',
    env: options.env ?? process.env,
  });
}

function sha256File(filePath) {
  return createHash('sha256').update(readFileSync(filePath)).digest('hex');
}

function writeFakeNpm(binPath, lines, exitCode = 17) {
  mkdirSync(binPath);
  writeFileSync(
    path.join(binPath, 'npm.cmd'),
    ['@echo off', ...lines.map((line) => `echo ${line}`), `exit /b ${exitCode}`, ''].join('\r\n'),
    'utf8',
  );
}

test('baseline requires two identical full-suite captures before acceptance', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'study-app-baseline-'));
  const firstPath = path.join(root, 'first.json');
  const secondPath = path.join(root, 'second.json');
  const acceptedPath = path.join(root, 'accepted.json');
  writeFileSync(firstPath, `${JSON.stringify(candidate())}\n`, 'utf8');
  writeFileSync(
    secondPath,
    `${JSON.stringify(candidate({ capturedAt: '2026-08-09T00:01:00.000Z' }))}\n`,
    'utf8',
  );

  const incomplete = runCli([
    'accept',
    '--first', firstPath,
    '--output', acceptedPath,
  ]);
  assert.notEqual(incomplete.status, 0);

  const accepted = runCli([
    'accept',
    '--first', firstPath,
    '--second', secondPath,
    '--output', acceptedPath,
  ]);
  assert.equal(accepted.status, 0, accepted.stderr);
  assert.deepEqual(JSON.parse(readFileSync(acceptedPath, 'utf8')), candidate());
});

test('baseline preserves the raw non-zero suite exit code', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'study-app-baseline-'));
  const binPath = path.join(root, 'bin');
  const outputPath = path.join(root, 'candidate.json');
  require('node:fs').mkdirSync(binPath);
  writeFileSync(
    path.join(binPath, 'npm.cmd'),
    [
      '@echo off',
      'echo FAIL src/components/workspace-shell/WorkspaceShell.test.tsx ^> WorkspaceShell ^> keeps selection',
      "echo AssertionError: expected 'first' to be 'second'",
      'echo  at src/components/workspace-shell/WorkspaceShell.test.tsx:41:7',
      'exit /b 17',
      '',
    ].join('\r\n'),
    'utf8',
  );

  const result = runCli(['capture', '--output', outputPath], {
    env: {
      ...process.env,
      PATH: `${binPath}${path.delimiter}${process.env.PATH ?? ''}`,
    },
  });

  assert.equal(result.status, 0, result.stderr);
  const captured = JSON.parse(readFileSync(outputPath, 'utf8'));
  assert.equal(captured.command, FULL_SUITE_COMMAND);
  assert.equal(captured.exitCode, 17);
});

test('guard rejects failed-test-id or normalized-stack drift', () => {
  const testPath = 'frontend/src/components/workspace-shell/WorkspaceShell.test.tsx';
  const root = mkdtempSync(path.join(tmpdir(), 'study-app-baseline-'));
  const baselinePath = path.join(root, 'baseline.json');
  const base = candidate({
    exitCode: 17,
    failedTestIds: [
      'src/components/workspace-shell/WorkspaceShell.test.tsx > WorkspaceShell > keeps selection',
    ],
    normalizedStackSignatures: [
      "AssertionError: expected 'first' to be 'second'\nat src/components/workspace-shell/WorkspaceShell.test.tsx",
    ],
    relatedFileSha256: [
      { path: testPath, sha256: sha256File(path.join(REPO_ROOT, testPath)) },
    ],
  });
  writeFileSync(baselinePath, `${JSON.stringify(base)}\n`, 'utf8');

  const cases = [
    {
      name: 'failed test ID',
      output: [
        'FAIL  src/components/workspace-shell/WorkspaceShell.test.tsx ^> WorkspaceShell ^> keeps focus',
        "AssertionError: expected 'first' to be 'second'",
        ' at src/components/workspace-shell/WorkspaceShell.test.tsx:99:3',
      ],
    },
    {
      name: 'normalized stack',
      output: [
        'FAIL  src/components/workspace-shell/WorkspaceShell.test.tsx ^> WorkspaceShell ^> keeps selection',
        "AssertionError: expected 'alpha' to be 'omega'",
        ' at src/components/workspace-shell/WorkspaceShell.test.tsx:99:3',
      ],
    },
  ];

  for (const driftCase of cases) {
    const binPath = path.join(root, driftCase.name.replaceAll(' ', '-'));
    writeFakeNpm(binPath, driftCase.output);
    const result = runCli(['verify', '--baseline', baselinePath], {
      env: {
        ...process.env,
        PATH: `${binPath}${path.delimiter}${process.env.PATH ?? ''}`,
      },
    });
    assert.equal(result.status, 2, `${driftCase.name}: ${result.stderr}`);
    assert.match(result.stderr, /PRE_EXISTING_FAILURE_BASELINE_DRIFT/);
  }
});

test('guard rejects a changed related-file hash or a slice touching that path', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'study-app-baseline-'));
  const binPath = path.join(root, 'hash-bin');
  const baselinePath = path.join(root, 'hash-baseline.json');
  const output = [
    'FAIL  src/components/workspace-shell/WorkspaceShell.test.tsx ^> WorkspaceShell ^> keeps selection',
    "AssertionError: expected 'first' to be 'second'",
    ' at src/components/workspace-shell/WorkspaceShell.test.tsx:19:4',
  ];
  writeFakeNpm(binPath, output);
  writeFileSync(
    baselinePath,
    `${JSON.stringify(candidate({
      exitCode: 17,
      failedTestIds: [
        'src/components/workspace-shell/WorkspaceShell.test.tsx > WorkspaceShell > keeps selection',
      ],
      normalizedStackSignatures: [
        "AssertionError: expected 'first' to be 'second'\nat src/components/workspace-shell/WorkspaceShell.test.tsx",
      ],
      relatedFileSha256: [{
        path: 'frontend/src/components/workspace-shell/WorkspaceShell.test.tsx',
        sha256: '0'.repeat(64),
      }],
    }))}\n`,
    'utf8',
  );
  const hashDrift = runCli(['verify', '--baseline', baselinePath], {
    env: { ...process.env, PATH: `${binPath}${path.delimiter}${process.env.PATH ?? ''}` },
  });
  assert.equal(hashDrift.status, 2, hashDrift.stderr);
  assert.match(hashDrift.stderr, /PRE_EXISTING_FAILURE_BASELINE_DRIFT/);

  const fixtureRoot = path.join(root, 'slice-fixture');
  const fixtureScripts = path.join(fixtureRoot, 'scripts');
  const fixtureSource = path.join(fixtureRoot, 'frontend', 'src', 'failure.test.ts');
  const fixtureBin = path.join(fixtureRoot, 'bin');
  const fixtureBaseline = path.join(fixtureRoot, 'baseline.json');
  mkdirSync(fixtureScripts, { recursive: true });
  mkdirSync(path.dirname(fixtureSource), { recursive: true });
  copyFileSync(CLI_PATH, path.join(fixtureScripts, 'pre-existing-failure-baseline.mjs'));
  writeFileSync(fixtureSource, 'export const state = "before";\n', 'utf8');
  assert.equal(spawnSync('git', ['init', '--quiet'], { cwd: fixtureRoot }).status, 0);
  assert.equal(spawnSync('git', ['add', '--', 'frontend/src/failure.test.ts'], { cwd: fixtureRoot }).status, 0);
  writeFileSync(fixtureSource, 'export const state = "after";\n', 'utf8');
  writeFakeNpm(fixtureBin, [
    'FAIL  src/failure.test.ts ^> suite ^> behavior',
    'AssertionError: expected true to be false',
    ' at src/failure.test.ts:7:2',
  ]);
  writeFileSync(
    fixtureBaseline,
    `${JSON.stringify(candidate({
      exitCode: 17,
      failedTestIds: ['src/failure.test.ts > suite > behavior'],
      normalizedStackSignatures: [
        'AssertionError: expected true to be false\nat src/failure.test.ts',
      ],
      relatedFileSha256: [{
        path: 'frontend/src/failure.test.ts',
        sha256: sha256File(fixtureSource),
      }],
    }))}\n`,
    'utf8',
  );
  const touched = runCli(['verify', '--baseline', fixtureBaseline], {
    cliPath: path.join(fixtureScripts, 'pre-existing-failure-baseline.mjs'),
    cwd: fixtureRoot,
    env: { ...process.env, PATH: `${fixtureBin}${path.delimiter}${process.env.PATH ?? ''}` },
  });
  assert.equal(touched.status, 2, touched.stderr);
  assert.match(touched.stderr, /PRE_EXISTING_FAILURE_BASELINE_RELATED_PATH_TOUCHED/);
});

test('zero-failure capture stores empty arrays', () => {
  const root = mkdtempSync(path.join(tmpdir(), 'study-app-baseline-'));
  const binPath = path.join(root, 'bin');
  const outputPath = path.join(root, 'candidate.json');
  writeFakeNpm(binPath, [
    'Test Files  12 passed (12)',
    'Tests  94 passed (94)',
  ], 0);

  const result = runCli(['capture', '--output', outputPath], {
    env: { ...process.env, PATH: `${binPath}${path.delimiter}${process.env.PATH ?? ''}` },
  });

  assert.equal(result.status, 0, result.stderr);
  const captured = JSON.parse(readFileSync(outputPath, 'utf8'));
  assert.equal(captured.exitCode, 0);
  assert.deepEqual(captured.failedTestIds, []);
  assert.deepEqual(captured.normalizedStackSignatures, []);
  assert.deepEqual(captured.relatedFileSha256, []);
});
