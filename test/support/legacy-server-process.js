const { fork } = require('node:child_process');
const { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const SERVER_PATH = path.join(REPO_ROOT, 'server.js');
const PRELOAD_PATH = path.join(REPO_ROOT, 'test', 'fixtures', 'legacy-server-preload.js');
const READY_TIMEOUT_MS = 10_000;
const STOP_TIMEOUT_MS = 2_000;

function timeoutAfter(milliseconds, message) {
  return new Promise((_, reject) => {
    const timer = setTimeout(() => reject(new Error(message)), milliseconds);
    timer.unref?.();
  });
}

function waitForExit(child, milliseconds) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve();
  return Promise.race([
    new Promise((resolve) => child.once('exit', resolve)),
    timeoutAfter(milliseconds, 'legacy server did not exit in time'),
  ]);
}

function removeOwnedTempRoot(tempRoot) {
  const resolved = path.resolve(tempRoot);
  const expectedParent = `${path.resolve(tmpdir())}${path.sep}`;
  if (!resolved.startsWith(expectedParent) || !path.basename(resolved).startsWith('study-app-legacy-server-')) {
    throw new Error(`refusing to remove unowned temp root: ${resolved}`);
  }
  if (existsSync(resolved)) rmSync(resolved, { recursive: true, force: true });
}

async function startLegacyServer({
  agentErrorCommands = [],
  titleTranslationDelayMs = 0,
  localRuntime = false,
} = {}) {
  if (!Array.isArray(agentErrorCommands) || agentErrorCommands.some((command) => typeof command !== 'string')) {
    throw new TypeError('agentErrorCommands must be an array of command names');
  }
  if (!Number.isInteger(titleTranslationDelayMs) || titleTranslationDelayMs < 0 || titleTranslationDelayMs > 10_000) {
    throw new TypeError('titleTranslationDelayMs must be an integer from 0 to 10000');
  }
  const tempRoot = mkdtempSync(path.join(tmpdir(), 'study-app-legacy-server-'));
  const databasePath = path.join(tempRoot, 'app.db');
  const settingsPath = path.join(tempRoot, 'settings.json');
  const pdfDir = path.join(tempRoot, 'pdfs');
  const explainerDir = path.join(tempRoot, 'explainers');
  const translationDir = path.join(tempRoot, 'translations');
  for (const directory of [pdfDir, explainerDir, translationDir]) mkdirSync(directory);
  writeFileSync(settingsPath, `${JSON.stringify({
    provider: 'openai',
    baseUrl: 'https://fixture.invalid/v1',
    model: 'fixture-model',
    apiKey: 'fixture-key',
    pdfDir,
    explainerDir,
    translationDir,
  })}\n`, 'utf8');
  const child = fork(SERVER_PATH, [], {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      DB_PATH: databasePath,
      SETTINGS_PATH: settingsPath,
      HOST: '127.0.0.1',
      PORT: '0',
      DISABLE_SCHEDULES: '1',
      API_BACKEND_MODE: 'legacy',
      DOCUMENT_PIPELINE_MODE: 'legacy',
      GENERATION_PIPELINE_MODE: 'legacy',
      ARTIFACT_READ_MODE: 'legacy',
      ARTIFACT_WRITE_MODE: 'legacy',
      OCR_ENABLED: '0',
      LEGACY_FIXTURE_AGENT_ERROR_COMMANDS: agentErrorCommands.join(','),
      LEGACY_FIXTURE_TITLE_TRANSLATION_DELAY_MS: String(titleTranslationDelayMs),
      PYTHONIOENCODING: 'utf-8',
      PYTHONUTF8: '1',
      ...(localRuntime ? { STUDY_APP_LOCAL_RUNTIME: '1' } : {}),
    },
    execArgv: ['--require', PRELOAD_PATH],
    stdio: ['ignore', 'pipe', 'pipe', 'ipc'],
  });
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (chunk) => { stdout += chunk.toString(); });
  child.stderr.on('data', (chunk) => { stderr += chunk.toString(); });

  try {
    const ready = await Promise.race([
      new Promise((resolve, reject) => {
        child.on('message', (message) => {
          if (message?.type === 'legacy-server-ready') resolve(message);
        });
        child.once('error', reject);
        child.once('exit', (code, signal) => reject(new Error(
          `legacy server exited before ready (${code ?? signal})\n${stdout}\n${stderr}`,
        )));
      }),
      timeoutAfter(READY_TIMEOUT_MS, `legacy server readiness timed out\n${stdout}\n${stderr}`),
    ]);
    if (!ready.address || ready.address.address !== '127.0.0.1' || !ready.address.port) {
      throw new Error(`legacy server reported an unsafe address: ${JSON.stringify(ready.address)}`);
    }

    let inspectionSequence = 0;
    return {
      address: ready.address,
      child,
      databasePath,
      explainerDir,
      pdfDir,
      translationDir,
      tempRoot,
      request(resource, init) {
        return fetch(`http://127.0.0.1:${ready.address.port}${resource}`, init);
      },
      inspect() {
        inspectionSequence += 1;
        const requestId = inspectionSequence;
        return Promise.race([
          new Promise((resolve, reject) => {
            const onMessage = (message) => {
              if (message?.type !== 'legacy-server-inspection' || message.requestId !== requestId) return;
              child.off('error', reject);
              child.off('message', onMessage);
              resolve(message);
            };
            child.on('message', onMessage);
            child.once('error', reject);
            child.send({ type: 'legacy-server-inspect', requestId });
          }),
          timeoutAfter(READY_TIMEOUT_MS, 'legacy server inspection timed out'),
        ]);
      },
      async stop() {
        if (child.exitCode === null && child.signalCode === null) {
          child.kill('SIGTERM');
          try {
            await waitForExit(child, STOP_TIMEOUT_MS);
          } catch (_) {
            child.kill('SIGKILL');
            await waitForExit(child, STOP_TIMEOUT_MS);
          }
        }
        child.stdout.destroy();
        child.stderr.destroy();
        removeOwnedTempRoot(tempRoot);
      },
    };
  } catch (error) {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    try { await waitForExit(child, STOP_TIMEOUT_MS); } catch (_) {}
    child.stdout.destroy();
    child.stderr.destroy();
    removeOwnedTempRoot(tempRoot);
    throw error;
  }
}

module.exports = { startLegacyServer };
