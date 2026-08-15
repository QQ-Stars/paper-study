"use strict";

const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const { existsSync, mkdtempSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  BackendRolloutConfigurationError,
  assertShadowRequestAllowed,
  isShadowReadOnly,
  loadBackendRollout,
  parseBackendRollout,
  rolloutToEnvironment,
} = require('../lib/backend-rollout');

const LEGACY_ENVIRONMENT = Object.freeze({
  API_BACKEND_MODE: 'legacy',
  DOCUMENT_PIPELINE_MODE: 'legacy',
  GENERATION_PIPELINE_MODE: 'legacy',
  ARTIFACT_READ_MODE: 'legacy',
  ARTIFACT_WRITE_MODE: 'legacy',
  OCR_ENABLED: '0',
});

const REPO_ROOT = path.resolve(__dirname, '..');

test('backend rollout rejects whitespace, mixed case, empty, and unknown values', () => {
  const variables = Object.keys(LEGACY_ENVIRONMENT);
  for (const variable of variables) {
    for (const value of ['', ' legacy', 'legacy ', 'LEGACY', 'unknown']) {
      assert.throws(
        () => loadBackendRollout({ [variable]: value }),
        (error) => error instanceof BackendRolloutConfigurationError
          && error.code === 'INVALID_ROLLOUT_VALUE'
          && error.variable === variable,
        `${variable}=${JSON.stringify(value)}`,
      );
    }
  }
});

test('unavailable adapters fail startup and shadow mode is read-only', () => {
  const unavailable = [
    ['API_BACKEND_MODE', 'shadow'],
    ['API_BACKEND_MODE', 'python'],
    ['DOCUMENT_PIPELINE_MODE', 'p1'],
    ['GENERATION_PIPELINE_MODE', 'p1'],
    ['ARTIFACT_READ_MODE', 'prefer_new'],
    ['ARTIFACT_WRITE_MODE', 'dual'],
    ['OCR_ENABLED', '1'],
  ];
  for (const [variable, value] of unavailable) {
    assert.throws(
      () => loadBackendRollout({ [variable]: value }),
      (error) => error instanceof BackendRolloutConfigurationError
        && error.code === 'ROLLOUT_ADAPTER_UNAVAILABLE'
        && error.variable === variable,
    );
  }

  const shadow = parseBackendRollout({ API_BACKEND_MODE: 'shadow' });
  assert.equal(isShadowReadOnly(shadow), true);
  assert.doesNotThrow(() => assertShadowRequestAllowed(shadow, 'GET'));
  assert.throws(
    () => assertShadowRequestAllowed(shadow, 'POST'),
    (error) => error instanceof BackendRolloutConfigurationError
      && error.code === 'SHADOW_MUTATION_FORBIDDEN',
  );
});

test('server rejects unavailable rollout before opening SQLite or binding', () => {
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'study-app-rollout-startup-'));
  const databasePath = path.join(tempRoot, 'app.db');
  try {
    const result = spawnSync(process.execPath, ['server.js'], {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        ...LEGACY_ENVIRONMENT,
        API_BACKEND_MODE: 'python',
        DB_PATH: databasePath,
        PORT: '0',
      },
      encoding: 'utf8',
      timeout: 3_000,
    });
    assert.notEqual(result.status, null, result.error?.message);
    assert.notEqual(result.status, 0);
    assert.match(result.stderr, /ROLLOUT_ADAPTER_UNAVAILABLE/);
    assert.equal(existsSync(databasePath), false);
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
});

test('absent backend rollout variables produce immutable P0 defaults', () => {
  const settings = loadBackendRollout({});
  assert.equal(Object.isFrozen(settings), true);
  assert.deepEqual(rolloutToEnvironment(settings), LEGACY_ENVIRONMENT);
  assert.throws(() => {
    settings.apiBackendMode = 'python';
  }, TypeError);
  assert.equal(settings.apiBackendMode, 'legacy');
});

test('P5 rollout extends the frozen P0.1 inventory only with OBSIDIAN_ENABLED', () => {
  assert.deepEqual(
    rolloutToEnvironment(parseBackendRollout({ OBSIDIAN_ENABLED: '1' })),
    LEGACY_ENVIRONMENT,
  );

  const environment = { OBSIDIAN_ENABLED: '1' };
  const settings = parseBackendRollout(environment, 'p5');
  environment.OBSIDIAN_ENABLED = '0';

  assert.equal(Object.isFrozen(settings), true);
  assert.equal(settings.obsidianEnabled, true);
  assert.deepEqual(rolloutToEnvironment(settings, 'p5'), {
    ...LEGACY_ENVIRONMENT,
    OBSIDIAN_ENABLED: '1',
  });
  assert.deepEqual(
    rolloutToEnvironment(parseBackendRollout({}, 'p5'), 'p5'),
    { ...LEGACY_ENVIRONMENT, OBSIDIAN_ENABLED: '0' },
  );

  for (const value of ['', 'true', 'yes', '2', ' 1']) {
    assert.throws(
      () => parseBackendRollout({ OBSIDIAN_ENABLED: value }, 'p5'),
      (error) => error instanceof BackendRolloutConfigurationError
        && error.code === 'INVALID_ROLLOUT_VALUE'
        && error.variable === 'OBSIDIAN_ENABLED',
      `OBSIDIAN_ENABLED=${JSON.stringify(value)}`,
    );
  }
});
