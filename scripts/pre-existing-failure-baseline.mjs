import { constants, existsSync, readFileSync, writeFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const VERSION = 'pre-existing-test-failures-v1';
const FULL_SUITE_COMMAND = 'npm.cmd run test:run --prefix frontend';
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const STABLE_FIELDS = [
  'command',
  'exitCode',
  'failedTestIds',
  'normalizedStackSignatures',
  'relatedFileSha256',
];

function fail(code, message) {
  process.stderr.write(`${code}: ${message}\n`);
  process.exitCode = 2;
}

function parseOptions(values) {
  const options = new Map();
  for (let index = 0; index < values.length; index += 2) {
    const name = values[index];
    const value = values[index + 1];
    if (!name?.startsWith('--') || value === undefined) {
      throw new Error('options must be supplied as --name value pairs');
    }
    options.set(name.slice(2), value);
  }
  return options;
}

function requireOption(options, name) {
  const value = options.get(name);
  if (!value) throw new Error(`missing required --${name}`);
  return value;
}

function assertSortedStrings(value, field) {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
    throw new Error(`${field} must be an array of strings`);
  }
  const sorted = [...value].sort((left, right) => left.localeCompare(right));
  if (JSON.stringify(value) !== JSON.stringify(sorted)) {
    throw new Error(`${field} must be sorted`);
  }
}

function assertRelatedFiles(value) {
  if (
    !Array.isArray(value)
    || value.some(
      (item) => !item
        || typeof item.path !== 'string'
        || typeof item.sha256 !== 'string'
        || !/^[a-f0-9]{64}$/.test(item.sha256),
    )
  ) {
    throw new Error('relatedFileSha256 must contain path/SHA-256 records');
  }
  const paths = value.map((item) => item.path);
  const sorted = [...paths].sort((left, right) => left.localeCompare(right));
  if (new Set(paths).size !== paths.length || JSON.stringify(paths) !== JSON.stringify(sorted)) {
    throw new Error('relatedFileSha256 must be unique and sorted by path');
  }
}

function readCandidate(candidatePath) {
  const value = JSON.parse(readFileSync(candidatePath, 'utf8'));
  if (value.version !== VERSION) throw new Error(`unsupported version in ${candidatePath}`);
  if (value.command !== FULL_SUITE_COMMAND) throw new Error(`unexpected command in ${candidatePath}`);
  if (!Number.isInteger(value.exitCode)) throw new Error(`exitCode must be an integer in ${candidatePath}`);
  assertSortedStrings(value.failedTestIds, 'failedTestIds');
  assertSortedStrings(value.normalizedStackSignatures, 'normalizedStackSignatures');
  assertRelatedFiles(value.relatedFileSha256);
  if (typeof value.capturedAt !== 'string' || !value.capturedAt) {
    throw new Error(`capturedAt must be a non-empty string in ${candidatePath}`);
  }
  if (typeof value.sourceTreeHash !== 'string' || !/^[a-f0-9]{64}$/.test(value.sourceTreeHash)) {
    throw new Error(`sourceTreeHash must be a SHA-256 in ${candidatePath}`);
  }
  return value;
}

function stripAnsi(value) {
  return value.replace(/\u001B\[[0-?]*[ -/]*[@-~]/g, '');
}

function repoRelativeSourcePath(value) {
  const normalized = value.replaceAll('\\', '/').replace(/^\.\//, '');
  const marker = normalized.toLowerCase().lastIndexOf('/study-app/');
  return marker >= 0 ? normalized.slice(marker + '/study-app/'.length) : normalized;
}

function normalizeDiagnosticLine(value) {
  let line = stripAnsi(value).trim();
  line = line.replace(/\((?:retry )?\d+\)/gi, '');
  line = line.replace(/\s+\d+(?:\.\d+)?(?:ms|s)\b/gi, '');
  line = line.replace(/(?:[A-Za-z]:)?[^\s()]*[\\/]study-app[\\/]/gi, '');
  line = line.replace(/((?:frontend|src|test|scripts|lib)[\\/][^\s():]+):\d+:\d+/g, '$1');
  line = repoRelativeSourcePath(line);
  return line.replaceAll('\\', '/').replace(/\s+/g, ' ').trim();
}

function parseSuiteOutput(rawOutput, exitCode) {
  if (exitCode === 0) {
    return { failedTestIds: [], normalizedStackSignatures: [], relatedPaths: [] };
  }

  const lines = stripAnsi(rawOutput).split(/\r?\n/);
  const failures = [];
  for (let index = 0; index < lines.length; index += 1) {
    const match = lines[index].trim().match(/^FAIL\s+(.+?)(?:\s+\[.+\])?$/);
    if (!match) continue;
    const id = normalizeDiagnosticLine(match[1]);
    const diagnostics = [];
    for (let next = index + 1; next < lines.length; next += 1) {
      const candidateLine = lines[next];
      if (/^\s*FAIL\s+/.test(candidateLine)) break;
      if (/^\s*(?:Test Files|Tests|Start at|Duration)\b/.test(candidateLine)) break;
      const normalized = normalizeDiagnosticLine(candidateLine);
      if (!normalized || /^[-=─]+$/.test(normalized)) continue;
      if (/^(?:Failed Tests|stdout|stderr)\b/i.test(normalized)) continue;
      diagnostics.push(normalized);
    }
    failures.push({ id, diagnostics });
  }

  if (failures.length === 0) {
    throw new Error('non-zero Vitest result did not contain a complete FAIL test ID');
  }

  const failedTestIds = [...new Set(failures.map((failure) => failure.id))].sort();
  const normalizedStackSignatures = [...new Set(
    failures.map((failure) => failure.diagnostics.join('\n')),
  )].sort();
  const sourcePattern = /(?:^|[\s(])((?:frontend\/)?(?:src|test|scripts|lib)\/[^\s():]+\.[cm]?[jt]sx?)/g;
  const relatedPaths = new Set();
  for (const failure of failures) {
    const values = [failure.id, ...failure.diagnostics];
    for (const value of values) {
      for (const match of value.matchAll(sourcePattern)) {
        const relative = match[1].startsWith('frontend/') ? match[1] : `frontend/${match[1]}`;
        if (existsSync(path.join(REPO_ROOT, relative))) relatedPaths.add(relative);
      }
    }
  }
  return {
    failedTestIds,
    normalizedStackSignatures,
    relatedPaths: [...relatedPaths].sort(),
  };
}

function sha256File(filePath) {
  return createHash('sha256').update(readFileSync(filePath)).digest('hex');
}

function sourceTreeHash() {
  const listed = spawnSync(
    'git',
    ['ls-files', '--cached', '--others', '--exclude-standard'],
    { cwd: REPO_ROOT, encoding: 'utf8' },
  );
  if (listed.error || listed.status !== 0) {
    throw listed.error ?? new Error(listed.stderr || 'git ls-files failed');
  }
  const digest = createHash('sha256');
  const paths = listed.stdout.split(/\r?\n/).filter(Boolean).sort();
  for (const relativePath of paths) {
    const absolutePath = path.join(REPO_ROOT, relativePath);
    if (!existsSync(absolutePath)) continue;
    digest.update(relativePath.replaceAll('\\', '/'));
    digest.update('\0');
    digest.update(sha256File(absolutePath));
    digest.update('\n');
  }
  return digest.digest('hex');
}

function runSuite() {
  const suite = spawnSync(FULL_SUITE_COMMAND, {
    cwd: REPO_ROOT,
    encoding: 'utf8',
    shell: true,
  });
  if (suite.error) throw suite.error;
  if (!Number.isInteger(suite.status)) throw new Error('Vitest did not return an integer exit code');
  const capturedOutput = `${suite.stdout ?? ''}\n${suite.stderr ?? ''}`;
  return { exitCode: suite.status, ...parseSuiteOutput(capturedOutput, suite.status) };
}

function observedCandidate() {
  const suite = runSuite();
  return {
    version: VERSION,
    command: FULL_SUITE_COMMAND,
    exitCode: suite.exitCode,
    failedTestIds: suite.failedTestIds,
    normalizedStackSignatures: suite.normalizedStackSignatures,
    relatedFileSha256: suite.relatedPaths.map((relativePath) => ({
      path: relativePath,
      sha256: sha256File(path.join(REPO_ROOT, relativePath)),
    })),
    capturedAt: new Date().toISOString(),
    sourceTreeHash: sourceTreeHash(),
  };
}

function accept(options) {
  const firstPath = path.resolve(requireOption(options, 'first'));
  const secondPath = path.resolve(requireOption(options, 'second'));
  const outputPath = path.resolve(requireOption(options, 'output'));
  if (firstPath === secondPath) {
    throw new Error('two independently stored candidates are required');
  }

  const first = readCandidate(firstPath);
  const second = readCandidate(secondPath);
  const stable = STABLE_FIELDS.every(
    (field) => JSON.stringify(first[field]) === JSON.stringify(second[field]),
  );
  if (!stable) {
    fail('PRE_EXISTING_FAILURE_BASELINE_UNSTABLE', 'captures differ');
    return;
  }

  writeFileSync(outputPath, `${JSON.stringify(first, null, 2)}\n`, {
    encoding: 'utf8',
    flag: constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY,
  });
}

function capture(options) {
  const outputPath = path.resolve(requireOption(options, 'output'));
  const value = observedCandidate();
  writeFileSync(outputPath, `${JSON.stringify(value, null, 2)}\n`, {
    encoding: 'utf8',
    flag: constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY,
  });
}

function verify(options) {
  const baseline = readCandidate(path.resolve(requireOption(options, 'baseline')));
  const observed = observedCandidate();
  const stable = STABLE_FIELDS.every(
    (field) => JSON.stringify(baseline[field]) === JSON.stringify(observed[field]),
  );
  if (!stable) {
    fail('PRE_EXISTING_FAILURE_BASELINE_DRIFT', 'observed suite differs from the baseline');
    return;
  }
  const diff = spawnSync('git', ['diff', '--name-only'], {
    cwd: REPO_ROOT,
    encoding: 'utf8',
  });
  if (diff.error || diff.status !== 0) {
    throw diff.error ?? new Error(diff.stderr || 'git diff --name-only failed');
  }
  const changedPaths = new Set(
    diff.stdout
      .split(/\r?\n/)
      .filter(Boolean)
      .map((value) => value.replaceAll('\\', '/')),
  );
  const touchedRelatedPaths = baseline.relatedFileSha256
    .map((entry) => entry.path)
    .filter((relatedPath) => changedPaths.has(relatedPath));
  if (touchedRelatedPaths.length > 0) {
    fail(
      'PRE_EXISTING_FAILURE_BASELINE_RELATED_PATH_TOUCHED',
      `current slice touches: ${touchedRelatedPaths.join(', ')}`,
    );
    return;
  }
  process.stdout.write(`${JSON.stringify({
    baselineMatched: true,
    observedSuiteExitCode: observed.exitCode,
    overallGreen: observed.exitCode === 0,
  })}\n`);
}

const [command, ...optionValues] = process.argv.slice(2);
try {
  const options = parseOptions(optionValues);
  if (command === 'capture') {
    capture(options);
  } else if (command === 'accept') {
    accept(options);
  } else if (command === 'verify') {
    verify(options);
  } else {
    throw new Error(`unsupported command: ${command ?? '<missing>'}`);
  }
} catch (error) {
  fail('PRE_EXISTING_FAILURE_BASELINE_INVALID', error.message);
}
