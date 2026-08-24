'use strict';

/*
 * Portable first-run launcher. It deliberately uses a separate database and
 * state directory so the P6 production owner gate is never bypassed.
 */
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');

const ROOT = path.resolve(__dirname, '..');
const DEFAULT_PORT = 5173;
const DEFAULT_STATE_DIR = path.join(ROOT, 'data', 'local-runtime');
const SERVER_PATH = path.join(ROOT, 'server.js');

function optionValue(args, name) {
  const index = args.indexOf(name);
  if (index < 0) return undefined;
  const value = args[index + 1];
  if (!value || value.startsWith('--')) {
    throw new Error(`${name} requires a value`);
  }
  return value;
}

function parsePort(value) {
  const port = Number(value ?? process.env.STUDY_APP_LOCAL_PORT ?? process.env.PORT ?? DEFAULT_PORT);
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`invalid local runtime port: ${value}`);
  }
  return port;
}

function localStateDirectory() {
  const configured = process.env.STUDY_APP_LOCAL_RUNTIME_DIR;
  return path.resolve(configured || DEFAULT_STATE_DIR);
}

function npmCommand() {
  return process.platform === 'win32' ? 'npm.cmd' : 'npm';
}

function hasNodeDependency() {
  try {
    require.resolve('better-sqlite3', { paths: [ROOT] });
    return true;
  } catch (_) {
    return false;
  }
}

function runCommand(command, args, label) {
  const result = spawnSync(command, args, {
    cwd: ROOT,
    stdio: 'inherit',
    shell: process.platform === 'win32' && command.toLowerCase() === 'npm.cmd',
    windowsHide: true,
  });
  if (result.error) throw new Error(`${label}: ${result.error.message}`);
  if (result.status !== 0) throw new Error(`${label} exited with code ${result.status}`);
}

function ensureDependencies({ installMissing, buildMissing }) {
  if (!hasNodeDependency()) {
    if (!installMissing) {
      throw new Error(
        'Node dependencies are missing. Run `npm ci` first, or rerun with --install-missing.',
      );
    }
    runCommand(npmCommand(), ['ci', '--omit=dev'], 'npm ci');
    if (!hasNodeDependency()) {
      throw new Error('npm ci completed but better-sqlite3 is still unavailable.');
    }
  }

  const workspaceIndex = path.join(ROOT, 'ui-redesign', 'dist', 'index.html');
  if (!fs.existsSync(workspaceIndex)) {
    if (!buildMissing) {
      throw new Error(
        'The workspace build is missing. Run `npm ci --prefix ui-redesign && npm run build --prefix ui-redesign`, or rerun with --build-missing.',
      );
    }
    runCommand(npmCommand(), ['ci', '--prefix', 'ui-redesign'], 'ui-redesign npm ci');
    runCommand(npmCommand(), ['run', 'build', '--prefix', 'ui-redesign'], 'ui-redesign build');
    if (!fs.existsSync(workspaceIndex)) {
      throw new Error('ui-redesign build completed without producing dist/index.html.');
    }
  }
}

function readPid(pidPath) {
  try {
    const raw = fs.readFileSync(pidPath, 'utf8').trim();
    try {
      const record = JSON.parse(raw);
      if (record && Number.isInteger(record.pid) && record.pid > 0) return record;
    } catch (_) {
      /* Keep accepting the original plain-integer PID file. */
    }
    const pid = Number.parseInt(raw, 10);
    return Number.isInteger(pid) && pid > 0 ? { pid } : null;
  } catch (_) {
    return null;
  }
}

function processExists(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (_) {
    return false;
  }
}

function terminateChild(child) {
  if (!child || child.exitCode !== null) return;
  if (process.platform === 'win32') {
    const result = spawnSync(
      'taskkill.exe',
      ['/PID', String(child.pid), '/T', '/F'],
      { stdio: 'ignore', windowsHide: true },
    );
    if (result.status === 0) return;
  }
  child.kill();
}

function probe(port, resource = '/api/papers') {
  return new Promise((resolve) => {
    const request = http.get(
      { host: '127.0.0.1', port, path: resource, timeout: 1000 },
      (response) => {
        response.resume();
        response.once('end', () => resolve(response.statusCode === 200));
      },
    );
    request.once('error', () => resolve(false));
    request.once('timeout', () => {
      request.destroy();
      resolve(false);
    });
  });
}

async function waitForReady(port, child, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (child && child.exitCode !== null) {
      throw new Error(`server exited before becoming ready (code ${child.exitCode})`);
    }
    if (await probe(port)) return;
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error(`local runtime did not become ready on http://127.0.0.1:${port}`);
}

function runtimeEnvironment(stateDir, port) {
  return {
    ...process.env,
    HOST: '127.0.0.1',
    PORT: String(port),
    UI_ENTRY: 'react',
    DB_PATH: process.env.STUDY_APP_LOCAL_DB_PATH || path.join(stateDir, 'app.db'),
    SETTINGS_PATH: process.env.STUDY_APP_LOCAL_SETTINGS_PATH || path.join(stateDir, 'settings.json'),
    STUDY_APP_LOCAL_RUNTIME: '1',
    API_BACKEND_MODE: 'legacy',
    DOCUMENT_PIPELINE_MODE: 'legacy',
    GENERATION_PIPELINE_MODE: 'legacy',
    ARTIFACT_READ_MODE: 'legacy',
    ARTIFACT_WRITE_MODE: 'legacy',
    OCR_ENABLED: '0',
    PYTHONUTF8: '1',
    PYTHONIOENCODING: 'utf-8',
  };
}

async function start({ detach, installMissing, buildMissing, port }) {
  ensureDependencies({ installMissing, buildMissing });
  const stateDir = localStateDirectory();
  fs.mkdirSync(stateDir, { recursive: true });
  const pidPath = path.join(stateDir, 'server.pid');
  const logPath = path.join(stateDir, 'server.log');
  const errorPath = path.join(stateDir, 'server.error.log');
  const existingPid = readPid(pidPath);

  if (existingPid && processExists(existingPid.pid)) {
    if (await probe(port)) {
      console.log(`Paper-Study local runtime is already running on http://127.0.0.1:${port}`);
      return 0;
    }
    throw new Error(`local runtime PID ${existingPid.pid} is active but port ${port} is not healthy; inspect ${errorPath}`);
  }
  if (existingPid) fs.rmSync(pidPath, { force: true });
  if (await probe(port)) {
    throw new Error(`port ${port} is already in use by another process`);
  }

  const environment = runtimeEnvironment(stateDir, port);
  const output = detach ? fs.openSync(logPath, 'a') : 'inherit';
  const error = detach ? fs.openSync(errorPath, 'a') : 'inherit';
  const child = spawn(process.execPath, [SERVER_PATH], {
    cwd: ROOT,
    env: environment,
    detached: detach,
    stdio: detach ? ['ignore', output, error] : 'inherit',
    windowsHide: detach,
  });
  if (detach) {
    fs.closeSync(output);
    fs.closeSync(error);
  }
  fs.writeFileSync(pidPath, `${JSON.stringify({
    version: 1,
    pid: child.pid,
    executable: process.execPath,
    serverPath: SERVER_PATH,
    port,
    startedAt: Date.now(),
  })}\n`, 'utf8');

  const forwardSignal = () => {
    if (!detach) terminateChild(child);
  };
  if (!detach) {
    process.once('SIGINT', forwardSignal);
    process.once('SIGTERM', forwardSignal);
  }

  try {
    await waitForReady(port, child);
  } catch (errorDuringStart) {
    terminateChild(child);
    if (!detach) {
      process.removeListener('SIGINT', forwardSignal);
      process.removeListener('SIGTERM', forwardSignal);
    }
    fs.rmSync(pidPath, { force: true });
    throw errorDuringStart;
  }

  if (!detach) {
    try {
      await new Promise((resolve) => child.once('exit', resolve));
      return child.exitCode || 0;
    } finally {
      process.removeListener('SIGINT', forwardSignal);
      process.removeListener('SIGTERM', forwardSignal);
      fs.rmSync(pidPath, { force: true });
    }
  }
  child.unref();
  console.log(`Paper-Study local runtime started: http://127.0.0.1:${port}/workspace/`);
  console.log(`Local data: ${environment.DB_PATH}`);
  return 0;
}

async function main() {
  const args = process.argv.slice(2);
  const detach = args.includes('--detach');
  const installMissing = args.includes('--install-missing');
  const buildMissing = args.includes('--build-missing');
  const port = parsePort(optionValue(args, '--port'));
  return start({ detach, installMissing, buildMissing, port });
}

main()
  .then((code) => process.exitCode = code)
  .catch((error) => {
    console.error(`Local startup failed: ${error.message}`);
    process.exitCode = 1;
  });
