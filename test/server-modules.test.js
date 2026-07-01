const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { PassThrough } = require('node:stream');
const test = require('node:test');

const {
  applySettingsUpdate,
  buildSettingsView,
  createSettingsStore,
  ensureSettingsDirs,
  maskKey,
  resolveDir
} = require('../lib/settings');
const {
  createArtifactLocator,
  planPdfArchive,
  scanPdfDirectory,
  titlePdfFilename,
  uniqueTitlePdfPath
} = require('../lib/artifacts');
const { createAgentRunner, createAgentEnv, resolvePythonExecutable } = require('../lib/agent-runner');
const { readBody, safeBase, send, startNdjson } = require('../lib/http');

function tempRoot() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'study-app-'));
}

test('settings store reads settings, env, masked keys, and llm config priority', () => {
  const root = tempRoot();
  const settingsPath = path.join(root, 'data', 'settings.json');
  fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
  fs.writeFileSync(settingsPath, JSON.stringify({
    provider: 'qwen',
    apiKey: 'settings-key-1234',
    model: 'settings-model'
  }));
  fs.writeFileSync(path.join(root, '.env'), [
    'LLM_PROVIDER=openai',
    'LLM_API_KEY=env-key-9999',
    'LLM_BASE_URL=https://env.example/v1',
    'LLM_MODEL=env-model'
  ].join('\n'));

  const store = createSettingsStore({ root, settingsPath });

  assert.deepEqual(store.read(), {
    provider: 'qwen',
    apiKey: 'settings-key-1234',
    model: 'settings-model'
  });
  assert.equal(store.readEnv().LLM_PROVIDER, 'openai');
  assert.equal(maskKey('abcdef1234'), '****1234');
  assert.equal(resolveDir(root, 'data/pdfs'), path.join(root, 'data/pdfs'));

  assert.deepEqual(store.llmConfig(), {
    apiKey: 'settings-key-1234',
    baseUrl: 'https://env.example/v1',
    model: 'settings-model'
  });
});

test('settings store falls back to provider preset when no setting or env value exists', () => {
  const root = tempRoot();
  const settingsPath = path.join(root, 'data', 'settings.json');
  fs.mkdirSync(path.dirname(settingsPath), { recursive: true });
  fs.writeFileSync(settingsPath, JSON.stringify({ provider: 'qwen' }));

  const store = createSettingsStore({ root, settingsPath });

  assert.deepEqual(store.llmConfig(), {
    apiKey: '',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
    model: 'qwen-plus'
  });
});

test('settings view masks secrets and resolves configured artifact directories', () => {
  const root = tempRoot();
  const defaults = {
    pdfDir: path.join(root, 'data', 'pdfs'),
    explainerDir: path.join(root, 'data', 'explainers'),
    translationDir: path.join(root, 'data', 'translations')
  };

  const view = buildSettingsView({
    root,
    defaultDirs: defaults,
    settings: {
      provider: 'qwen',
      apiKey: 'settings-key-1234',
      s2ApiKey: 's2-key-5555',
      pdfDir: 'custom-pdfs',
      researchTheme: 'multimodal reasoning',
      embedProvider: 'remote',
      embedApiKey: 'embed-key-7777'
    },
    env: {
      LLM_PROVIDER: 'openai',
      LLM_BASE_URL: 'https://env.example/v1',
      LLM_MODEL: 'env-model',
      LLM_API_KEY: 'env-key-9999'
    }
  });

  assert.equal(view.provider, 'qwen');
  assert.equal(view.baseUrl, 'https://env.example/v1');
  assert.equal(view.model, 'env-model');
  assert.equal(view.apiKeyTail, '****1234');
  assert.equal(view.s2KeyTail, '****5555');
  assert.equal(view.embedKeyTail, '****7777');
  assert.equal(view.hasApiKey, true);
  assert.equal(view.defaultPdfDir, path.join('data', 'pdfs'));
  assert.equal(view.resolvedPdfDir, path.join(root, 'custom-pdfs'));
  assert.equal(view.resolvedExplainerDir, defaults.explainerDir);
  assert.equal(view.researchTheme, 'multimodal reasoning');
});

test('settings update keeps existing secrets when patch values are empty and creates configured dirs', () => {
  const root = tempRoot();
  const current = {
    provider: 'deepseek',
    apiKey: 'old-api-key',
    s2ApiKey: 'old-s2-key',
    embedApiKey: 'old-embed-key',
    pdfDir: 'old-pdfs'
  };

  const next = applySettingsUpdate(current, {
    provider: 'qwen',
    baseUrl: 'https://new.example/v1',
    model: 'qwen-plus',
    apiKey: '',
    s2ApiKey: '',
    embedApiKey: '',
    pdfDir: '  new-pdfs  ',
    explainerDir: ' explainers ',
    translationDir: '',
    researchTheme: ' academic reading ',
    embedProvider: 'remote',
    embedApiBase: ' https://embed.example ',
    embedApiModel: ' embed-model '
  });

  assert.equal(next.provider, 'qwen');
  assert.equal(next.apiKey, 'old-api-key');
  assert.equal(next.s2ApiKey, 'old-s2-key');
  assert.equal(next.embedApiKey, 'old-embed-key');
  assert.equal(next.pdfDir, 'new-pdfs');
  assert.equal(next.explainerDir, 'explainers');
  assert.equal(next.translationDir, '');
  assert.equal(next.researchTheme, 'academic reading');
  assert.equal(next.embedApiBase, 'https://embed.example');
  assert.equal(next.embedApiModel, 'embed-model');

  ensureSettingsDirs(next, { root });

  assert.equal(fs.statSync(path.join(root, 'new-pdfs')).isDirectory(), true);
  assert.equal(fs.statSync(path.join(root, 'explainers')).isDirectory(), true);
  assert.equal(fs.existsSync(path.join(root, 'old-pdfs')), false);
});

test('artifact locator resolves pdfs with row path, custom directory, default directory, and seed directory', () => {
  const root = tempRoot();
  const defaultPdfDir = path.join(root, 'data', 'pdfs');
  const seedPdfDir = path.join(root, '..', 'paper');
  const customDir = path.join(root, 'custom-pdfs');
  fs.mkdirSync(defaultPdfDir, { recursive: true });
  fs.mkdirSync(seedPdfDir, { recursive: true });
  fs.mkdirSync(customDir, { recursive: true });

  const explicit = path.join(root, 'explicit.pdf');
  const custom = path.join(customDir, 'custom-id.pdf');
  const fallback = path.join(defaultPdfDir, 'fallback-id.pdf');
  const seed = path.join(seedPdfDir, 'seed-id.pdf');
  fs.writeFileSync(explicit, 'pdf');
  fs.writeFileSync(custom, 'pdf');
  fs.writeFileSync(fallback, 'pdf');
  fs.writeFileSync(seed, 'pdf');

  const locator = createArtifactLocator({
    root,
    defaultPdfDir,
    seedPdfDir,
    settingsStore: { read: () => ({ pdfDir: customDir }) },
    getPdfPath: (id) => id === 'explicit-id' ? explicit : null
  });

  assert.equal(locator.resolvePdfById('explicit-id'), explicit);
  assert.equal(locator.resolvePdfById('custom-id'), custom);
  assert.equal(locator.resolvePdfById('fallback-id'), fallback);
  assert.equal(locator.resolvePdfById('seed-id'), seed);
  assert.equal(locator.resolvePdfById('missing-id'), null);

  assert.equal(locator.hasPdfForRow({ id: 'explicit-id', pdf_path: explicit }), true);
  assert.equal(locator.hasPdfForRow({ id: 'fallback-id' }), true);
  assert.equal(locator.hasPdfForRow({ id: 'missing-id' }), false);
});

test('scanPdfDirectory lists pdf files recursively, skips hidden directories, and sorts paths', () => {
  const root = tempRoot();
  const dir = path.join(root, 'library');
  const nested = path.join(dir, 'topic', 'subtopic');
  const hidden = path.join(dir, '.cache');
  fs.mkdirSync(nested, { recursive: true });
  fs.mkdirSync(hidden, { recursive: true });

  const first = path.join(dir, 'b-paper.pdf');
  const second = path.join(nested, 'a-paper.PDF');
  fs.writeFileSync(first, 'one');
  fs.writeFileSync(second, 'two');
  fs.writeFileSync(path.join(hidden, 'hidden.pdf'), 'nope');
  fs.writeFileSync(path.join(dir, 'notes.txt'), 'nope');

  const result = scanPdfDirectory(dir);

  assert.equal(result.ok, true);
  assert.equal(result.dir, dir);
  assert.equal(result.count, 2);
  assert.deepEqual(result.files.map((file) => file.path), [first, second].sort((a, b) => a.localeCompare(b)));
  assert.deepEqual(result.files.map((file) => file.name), ['b-paper.pdf', 'a-paper.PDF']);
  assert.equal(result.files.every((file) => file.size > 0), true);
});

test('paper PDF filenames are derived from safe paper titles with stable collision suffixes', () => {
  assert.equal(
    titlePdfFilename('Chain-of-Verification: Reduces Hallucination? / Test*'),
    'Chain-of-Verification Reduces Hallucination Test.pdf'
  );
  assert.equal(titlePdfFilename('   ...   ', { fallback: 'paper-id' }), 'paper-id.pdf');
  assert.equal(titlePdfFilename('CON'), '_CON.pdf');

  const root = tempRoot();
  const pdfDir = path.join(root, 'data', 'pdfs');
  fs.mkdirSync(pdfDir, { recursive: true });
  const taken = path.join(pdfDir, 'A Study.pdf');
  fs.writeFileSync(taken, 'existing');

  assert.equal(
    uniqueTitlePdfPath(pdfDir, 'A Study', { id: '2024.12345' }),
    path.join(pdfDir, 'A Study - 2024.12345.pdf')
  );
});

test('PDF archive plan copies external files and moves project-local PDFs into title names', () => {
  const root = tempRoot();
  const pdfDir = path.join(root, 'data', 'pdfs');
  const outside = path.join(root, 'outside', 'source.pdf');
  const local = path.join(pdfDir, 'old-id.pdf');
  fs.mkdirSync(path.dirname(outside), { recursive: true });
  fs.mkdirSync(pdfDir, { recursive: true });
  fs.writeFileSync(outside, 'external');
  fs.writeFileSync(local, 'local');

  const externalPlan = planPdfArchive({
    pdfDir,
    sourcePath: outside,
    title: 'External Paper',
    id: 'external-id'
  });
  assert.equal(externalPlan.action, 'copy');
  assert.equal(externalPlan.targetPath, path.join(pdfDir, 'External Paper.pdf'));

  const localPlan = planPdfArchive({
    pdfDir,
    sourcePath: local,
    title: 'Local Paper',
    id: 'local-id'
  });
  assert.equal(localPlan.action, 'move');
  assert.equal(localPlan.targetPath, path.join(pdfDir, 'Local Paper.pdf'));
});

test('agent runner resolves python executable and spawns agent module with UTF-8 env', () => {
  const root = tempRoot();
  const venvPython = path.join(root, '.venv', 'Scripts', 'python.exe');
  fs.mkdirSync(path.dirname(venvPython), { recursive: true });
  fs.writeFileSync(venvPython, '');

  assert.equal(resolvePythonExecutable({ root, platform: 'win32' }), venvPython);
  assert.equal(resolvePythonExecutable({ root: tempRoot(), platform: 'linux' }), 'python3');
  assert.deepEqual(createAgentEnv({ KEEP: 'yes' }), {
    KEEP: 'yes',
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1'
  });

  let captured = null;
  const runner = createAgentRunner({
    root,
    baseEnv: { KEEP: 'yes' },
    spawnImpl: (command, args, options) => {
      captured = { command, args, options };
      return { pid: 1 };
    }
  });

  const child = runner.spawn(['search', '--query', 'mllm']);

  assert.deepEqual(child, { pid: 1 });
  assert.equal(captured.command, venvPython);
  assert.deepEqual(captured.args, ['-m', 'agent', 'search', '--query', 'mllm']);
  assert.equal(captured.options.cwd, root);
  assert.equal(captured.options.env.PYTHONUTF8, '1');
  assert.equal(captured.options.env.KEEP, 'yes');
});

test('http helpers send text, sanitize basenames, read request bodies, and stream ndjson', async () => {
  const sent = {};
  const response = {
    writeHead(code, headers) { sent.code = code; sent.headers = headers; },
    end(body) { sent.body = body; },
    writes: [],
    write(chunk) { this.writes.push(chunk); }
  };

  send(response, 201, 'created', 'text/plain; charset=utf-8');
  assert.equal(sent.code, 201);
  assert.equal(sent.headers['Content-Type'], 'text/plain; charset=utf-8');
  assert.equal(sent.body, 'created');
  assert.equal(safeBase('../paper.pdf'), 'paper.pdf');

  const req = new PassThrough();
  const bodyPromise = readBody(req);
  req.end('{"ok":true}');
  assert.equal(await bodyPromise, '{"ok":true}');

  const emit = startNdjson(response);
  emit({ type: 'progress', line: 'hello' });
  assert.equal(sent.code, 200);
  assert.equal(sent.headers['Content-Type'], 'application/x-ndjson; charset=utf-8');
  assert.equal(sent.headers['Cache-Control'], 'no-cache');
  assert.equal(response.writes.at(-1), '{"type":"progress","line":"hello"}\n');
});
