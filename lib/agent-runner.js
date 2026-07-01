const fs = require('fs');
const path = require('path');
const { spawn } = require('child_process');

function resolvePythonExecutable({
  root = process.cwd(),
  fsImpl = fs,
  platform = process.platform,
} = {}) {
  for (const candidate of [
    path.join(root, '.venv', 'Scripts', 'python.exe'),
    path.join(root, '.venv', 'bin', 'python'),
  ]) {
    if (fsImpl.existsSync(candidate)) return candidate;
  }
  return platform === 'win32' ? 'python' : 'python3';
}

function createAgentEnv(baseEnv = process.env) {
  return {
    ...baseEnv,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
  };
}

function createAgentRunner({
  root = process.cwd(),
  fsImpl = fs,
  platform = process.platform,
  baseEnv = process.env,
  spawnImpl = spawn,
} = {}) {
  const pythonExecutable = () => resolvePythonExecutable({ root, fsImpl, platform });
  const spawnAgent = (args, opts = {}) => spawnImpl(
    pythonExecutable(),
    ['-m', 'agent', ...args],
    { cwd: root, env: createAgentEnv(baseEnv), ...opts }
  );

  return { pythonExecutable, spawn: spawnAgent };
}

module.exports = {
  createAgentEnv,
  createAgentRunner,
  resolvePythonExecutable,
};
