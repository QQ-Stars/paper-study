const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const {
  REACT_CSP,
  createFrontendConfig,
  extractRawPathname,
  resolveFrontendPath,
  selectRoutingPathname,
  serveFrontendRequest,
} = require('../lib/frontend-assets');

function writeFile(file, contents = '') {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, contents);
}

function createFixture() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'study-app-frontend-'));
  const react = path.join(root, 'frontend', 'dist');
  const legacy = path.join(root, 'public');
  const reactIndex = path.join(react, 'index.html');
  const legacyIndex = path.join(legacy, 'index.html');

  writeFile(reactIndex, '<div id="root"></div>');
  writeFile(path.join(react, 'assets', 'index-AbC_d123.js'), 'hashed');
  writeFile(path.join(react, 'assets', 'license-apache20.txt'), 'not a content hash');
  writeFile(path.join(react, 'assets', 'theme.css'), 'plain');
  writeFile(path.join(react, 'reader-worker.mjs'), 'worker');
  writeFile(path.join(react, 'fonts', 'research.woff2'), 'font');
  writeFile(path.join(react, 'runtime.wasm'), 'wasm');
  writeFile(path.join(react, '.vite', 'manifest.json'), JSON.stringify({
    'index.html': {
      file: 'assets/index-AbC_d123.js',
      isEntry: true,
    },
  }));

  writeFile(legacyIndex, '<main>legacy</main>');
  writeFile(path.join(legacy, 'style.css'), 'legacy style');
  writeFile(path.join(legacy, 'app.js'), 'legacy app');
  writeFile(path.join(legacy, 'vendor', 'pdfjs', 'pdf.worker.min.js'), 'pdf worker');

  return {
    root,
    roots: { react, reactIndex, legacy, legacyIndex },
  };
}

const fixture = createFixture();
test.after(() => fs.rmSync(fixture.root, { recursive: true, force: true }));

const reactConfig = createFrontendConfig({
  roots: fixture.roots,
  uiEntry: 'react',
  warn: () => {},
});

function resolve(pathname, overrides = {}) {
  return resolveFrontendPath(pathname, fixture.roots, {
    ...reactConfig,
    ...overrides,
  });
}

async function requestFrontend(pathname, { method = 'GET', fileOps } = {}) {
  const server = http.createServer((req, res) => {
    serveFrontendRequest(
      req,
      res,
      extractRawPathname(req.url),
      fixture.roots,
      reactConfig,
      fileOps,
    );
  });
  await new Promise((resolveListen) => server.listen(0, '127.0.0.1', resolveListen));
  try {
    const address = server.address();
    const response = await fetch(`http://127.0.0.1:${address.port}${pathname}`, {
      headers: { Connection: 'close' },
      method,
      redirect: 'manual',
    });
    return {
      body: await response.text(),
      headers: response.headers,
      status: response.status,
    };
  } finally {
    await new Promise((resolveClose, rejectClose) => server.close((error) => {
      if (error) rejectClose(error);
      else resolveClose();
    }));
  }
}

test('UI_ENTRY defaults to React and is read into an immutable startup decision', () => {
  const warnings = [];
  const config = createFrontendConfig({
    roots: fixture.roots,
    uiEntry: undefined,
    warn: (message) => warnings.push(message),
  });

  assert.equal(config.requestedEntry, 'react');
  assert.equal(config.rootEntry, 'react');
  assert.equal(config.reactAvailable, true);
  assert.deepEqual(warnings, []);
  assert.equal(Object.isFrozen(config), true);
});

test('invalid UI_ENTRY warns clearly and defaults to React', () => {
  const warnings = [];
  const config = createFrontendConfig({
    roots: fixture.roots,
    uiEntry: 'preview',
    warn: (message) => warnings.push(message),
  });

  assert.equal(config.requestedEntry, 'react');
  assert.equal(config.rootEntry, 'react');
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /UI_ENTRY/i);
  assert.match(warnings[0], /preview/);
  assert.match(warnings[0], /react/);
});

test('a missing React build only falls the root back to legacy and warns at startup', () => {
  const warnings = [];
  const missingReact = path.join(fixture.root, 'missing-react-dist');
  const roots = {
    ...fixture.roots,
    react: missingReact,
    reactIndex: path.join(missingReact, 'index.html'),
  };
  const config = createFrontendConfig({
    roots,
    uiEntry: 'react',
    warn: (message) => warnings.push(message),
  });

  assert.equal(config.requestedEntry, 'react');
  assert.equal(config.rootEntry, 'legacy');
  assert.equal(config.reactAvailable, false);
  assert.equal(resolveFrontendPath('/', roots, config).kind, 'legacy-html');
  assert.equal(resolveFrontendPath('/workspace/', roots, config).kind, 'unavailable');
  assert.equal(resolveFrontendPath('/workspace/library', roots, config).kind, 'unavailable');
  assert.equal(resolveFrontendPath('/legacy/', roots, config).kind, 'legacy-html');
  assert.equal(warnings.length, 1);
  assert.match(warnings[0], /React/i);
  assert.match(warnings[0], /legacy/i);
});

test('the root switch changes only slash', () => {
  const reactRoot = resolve('/');
  const legacyRoot = resolve('/', { rootEntry: 'legacy' });

  assert.equal(reactRoot.kind, 'redirect');
  assert.equal(reactRoot.status, 302);
  assert.equal(reactRoot.headers.Location, '/workspace/');
  assert.equal(reactRoot.headers['Cache-Control'], 'no-store');
  assert.equal(legacyRoot.kind, 'legacy-html');
  assert.equal(resolve('/workspace/library', { rootEntry: 'legacy' }).kind, 'react-html');
  assert.equal(resolve('/legacy/style.css', { rootEntry: 'react' }).kind, 'legacy-file');
});

test('explicit entry roots redirect to canonical trailing-slash URLs', () => {
  for (const [pathname, location] of [
    ['/workspace', '/workspace/'],
    ['/legacy', '/legacy/'],
  ]) {
    const result = resolve(pathname);
    assert.equal(result.kind, 'redirect');
    assert.equal(result.status, 302);
    assert.equal(result.headers.Location, location);
    assert.equal(result.headers['Cache-Control'], 'no-store');
  }
});

test('a dotted Reader deep link resolves to the React index, not a guessed file', () => {
  const result = resolve('/workspace/reader/2401.12345');

  assert.equal(result.kind, 'react-html');
  assert.equal(result.file, fixture.roots.reactIndex);
  assert.equal(result.headers['Content-Type'], 'text/html; charset=utf-8');
  assert.equal(result.headers['Cache-Control'], 'no-cache');
  assert.equal(result.headers['Content-Security-Policy'], REACT_CSP);
});

test('React serves real files and applies immutable caching only to Vite-hashed assets', () => {
  const hashed = resolve('/workspace/assets/index-AbC_d123.js');
  const hashLookalike = resolve('/workspace/assets/license-apache20.txt');
  const nonHashed = resolve('/workspace/assets/theme.css');
  const worker = resolve('/workspace/reader-worker.mjs');
  const font = resolve('/workspace/fonts/research.woff2');
  const wasm = resolve('/workspace/runtime.wasm');

  assert.equal(hashed.kind, 'react-file');
  assert.equal(hashed.headers['Content-Type'], 'text/javascript; charset=utf-8');
  assert.equal(hashed.headers['Cache-Control'], 'public,max-age=31536000,immutable');
  assert.equal(hashLookalike.headers['Cache-Control'], 'no-cache');
  assert.equal(nonHashed.kind, 'react-file');
  assert.equal(nonHashed.headers['Cache-Control'], 'no-cache');
  assert.equal(worker.headers['Content-Type'], 'text/javascript; charset=utf-8');
  assert.equal(worker.headers['Cache-Control'], 'no-cache');
  assert.equal(font.headers['Content-Type'], 'font/woff2');
  assert.equal(wasm.headers['Content-Type'], 'application/wasm');
  for (const result of [hashed, hashLookalike, nonHashed, worker, font, wasm]) {
    assert.equal('Content-Security-Policy' in result.headers, false);
  }
});

test('a missing or malformed Vite manifest falls back to conservative caching', () => {
  const missingManifestRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'study-app-no-manifest-'));
  const react = path.join(missingManifestRoot, 'frontend', 'dist');
  const legacy = path.join(missingManifestRoot, 'public');
  writeFile(path.join(react, 'index.html'), '<div id="root"></div>');
  writeFile(path.join(react, 'assets', 'index-AbC_d123.js'), 'asset');
  writeFile(path.join(legacy, 'index.html'), 'legacy');
  const roots = { react, legacy };
  const config = createFrontendConfig({ roots, uiEntry: 'react', warn: () => {} });

  try {
    const result = resolveFrontendPath('/workspace/assets/index-AbC_d123.js', roots, config);
    assert.equal(result.headers['Cache-Control'], 'no-cache');
  } finally {
    fs.rmSync(missingManifestRoot, { recursive: true, force: true });
  }
});

test('the real frontend server permits reads and rejects write methods', async () => {
  const get = await requestFrontend('/workspace/');
  const head = await requestFrontend('/workspace/assets/index-AbC_d123.js', { method: 'HEAD' });
  const post = await requestFrontend('/workspace/', { method: 'POST' });

  assert.equal(get.status, 200);
  assert.match(get.body, /id="root"/u);
  assert.equal(head.status, 200);
  assert.equal(head.body, '');
  assert.equal(head.headers.get('content-length'), String('hashed'.length));
  assert.equal(post.status, 405);
  assert.equal(post.headers.get('allow'), 'GET, HEAD');
  assert.equal(post.headers.get('cache-control'), 'no-store');
});

test('an asset removed between resolution and open returns a controlled response', async () => {
  const volatile = path.join(fixture.roots.react, 'assets', 'volatile.txt');
  writeFile(volatile, 'temporary');
  let removed = false;
  const fileOps = {
    ...fs,
    open(file, flags, callback) {
      if (!removed && path.resolve(file) === path.resolve(volatile)) {
        removed = true;
        fs.rmSync(volatile);
      }
      fs.open(file, flags, callback);
    },
  };

  const response = await requestFrontend('/workspace/assets/volatile.txt', { fileOps });

  assert.equal(response.status, 404);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.match(response.body, /not found/u);
});

test('a missing Vite asset is a 404 while non-asset deep routes use the SPA index', () => {
  const asset = resolve('/workspace/assets/missing.js');
  const dottedRoute = resolve('/workspace/reader/not-a-file.pdf');

  assert.equal(asset.kind, 'not-found');
  assert.equal(asset.status, 404);
  assert.equal(dottedRoute.kind, 'react-html');
  assert.equal(dottedRoute.file, fixture.roots.reactIndex);
});

test('legacy alias preserves its index, assets, and no-cache behavior without React CSP', () => {
  for (const pathname of ['/legacy/', '/legacy/index.html']) {
    const result = resolve(pathname);
    assert.equal(result.kind, 'legacy-html');
    assert.equal(result.file, fixture.roots.legacyIndex);
    assert.equal(result.headers['Content-Type'], 'text/html; charset=utf-8');
    assert.equal(result.headers['Cache-Control'], 'no-cache');
    assert.equal('Content-Security-Policy' in result.headers, false);
  }

  const style = resolve('/legacy/style.css');
  assert.equal(style.kind, 'legacy-file');
  assert.equal(style.headers['Content-Type'], 'text/css; charset=utf-8');
  assert.equal(style.headers['Cache-Control'], 'no-cache');
  assert.equal('Content-Security-Policy' in style.headers, false);
});

test('unprefixed legacy assets remain available as the final public-file fallback', () => {
  for (const pathname of ['/style.css', '/app.js', '/vendor/pdfjs/pdf.worker.min.js']) {
    const result = resolve(pathname);
    assert.equal(result.kind, 'legacy-file', pathname);
    assert.equal(result.headers['Cache-Control'], 'no-cache', pathname);
    assert.equal('Content-Security-Policy' in result.headers, false, pathname);
  }
});

test('reserved backend prefixes and unknown API routes never become React HTML', () => {
  for (const pathname of ['/api/not-real', '/pdfbytes', '/papers/missing.pdf']) {
    const result = resolve(pathname);
    assert.equal(result.kind, 'not-found', pathname);
  }
});

test('malformed encodings, NULs, dot segments, encoded separators, and absolute paths are forbidden', () => {
  const unsafePaths = [
    '/workspace/%',
    '/workspace/%C0%AF',
    '/workspace/evil\0.js',
    '/workspace/evil%00.js',
    '/workspace/../server.js',
    '/workspace/./library',
    '/workspace/%2e%2e/server.js',
    '/workspace/%2E/library',
    '/workspace/a%2fb.js',
    '/workspace/a%5Cb.js',
    '/workspace/a\\b.js',
    '/workspace//server.js',
    '/workspace/C:/Windows/system.ini',
    '/legacy/%2e%2e/server.js',
    '/../server.js',
    '/%2e%2e/server.js',
    '//server/share/file.js',
  ];

  for (const pathname of unsafePaths) {
    assert.equal(resolve(pathname).kind, 'forbidden', pathname);
  }
});

test('frontend prefix collisions are rejected instead of falling through to public files', () => {
  assert.equal(resolve('/workspace-old/index.html').kind, 'forbidden');
  assert.equal(resolve('/legacy-backup/style.css').kind, 'forbidden');
});

test('realpath containment rejects an existing symlink that escapes a frontend root', (t) => {
  const outside = path.join(fixture.root, 'outside-assets');
  const link = path.join(fixture.roots.react, 'assets', 'escape');
  writeFile(path.join(outside, 'secret.js'), 'secret');
  try {
    fs.symlinkSync(outside, link, process.platform === 'win32' ? 'junction' : 'dir');
  } catch (error) {
    t.skip(`symlink creation is unavailable: ${error.code || error.message}`);
    return;
  }

  assert.equal(resolve('/workspace/assets/escape/secret.js').kind, 'forbidden');
});

test('raw request-target extraction strips query data without URL path normalization', () => {
  assert.equal(extractRawPathname('/workspace/reader/2401.12345?tab=notes'), '/workspace/reader/2401.12345');
  assert.equal(extractRawPathname('/workspace/../server.js?x=1'), '/workspace/../server.js');
  assert.equal(extractRawPathname('http://example.test/workspace/'), 'http://example.test/workspace/');
});

test('backend routing stays URL-compatible while explicit frontend candidates retain their raw path', () => {
  assert.equal(selectRoutingPathname('/api/papers', '/api/papers'), '/api/papers');
  assert.equal(selectRoutingPathname('/older/../api/papers', '/api/papers'), '/api/papers');
  assert.equal(
    selectRoutingPathname('/workspace/%2e%2e/api/papers', '/api/papers'),
    '/workspace/%2e%2e/api/papers',
  );
  assert.equal(
    selectRoutingPathname('/legacy\\..\\api/papers', '/api/papers'),
    '/legacy\\..\\api/papers',
  );
});

test('server wires raw frontend routing after papers while preserving the public root', () => {
  const source = fs.readFileSync(path.join(__dirname, '..', 'server.js'), 'utf8');
  const unknownApiBranch = source.indexOf("if (p === '/api' || p.startsWith('/api/'))");
  const pdfBytesBranch = source.indexOf("if (p === '/pdfbytes')");
  const papersBranch = source.indexOf("if (p.startsWith('/papers/'))");
  const frontendDispatch = source.indexOf('serveFrontendRequest');

  assert.notEqual(unknownApiBranch, -1);
  assert.notEqual(pdfBytesBranch, -1);
  assert.notEqual(papersBranch, -1);
  assert.notEqual(frontendDispatch, -1);
  assert.ok(unknownApiBranch < pdfBytesBranch, 'unknown /api/* must terminate before /pdfbytes');
  assert.ok(pdfBytesBranch < papersBranch, '/pdfbytes must remain before /papers/*');
  assert.ok(frontendDispatch > papersBranch, 'frontend dispatch must remain after /papers/*');
  assert.match(source, /extractRawPathname\(req\.url\)/);
  assert.match(source, /serveFrontendRequest\([^;]*rawPathname/);
  assert.match(source, /legacy:\s*PUBLIC/);
  assert.match(source, /UI_ENTRY\s*=\s*process\.env\.UI_ENTRY/);
});
