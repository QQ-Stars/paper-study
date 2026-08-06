const fs = require('node:fs');
const path = require('node:path');
const { pipeline } = require('node:stream');

const REACT_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "worker-src 'self'",
  "connect-src 'self'",
  "style-src 'self' 'unsafe-inline'",
  "font-src 'self' data:",
  "object-src 'none'",
  "base-uri 'self'",
  "frame-ancestors 'none'",
].join('; ');

const FRONTEND_MIME = Object.freeze({
  '.avif': 'image/avif',
  '.css': 'text/css; charset=utf-8',
  '.gif': 'image/gif',
  '.html': 'text/html; charset=utf-8',
  '.ico': 'image/x-icon',
  '.jpeg': 'image/jpeg',
  '.jpg': 'image/jpeg',
  '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
  '.md': 'text/markdown; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.cjs': 'text/javascript; charset=utf-8',
  '.otf': 'font/otf',
  '.pdf': 'application/pdf',
  '.png': 'image/png',
  '.svg': 'image/svg+xml',
  '.ttf': 'font/ttf',
  '.txt': 'text/plain; charset=utf-8',
  '.wasm': 'application/wasm',
  '.webmanifest': 'application/manifest+json; charset=utf-8',
  '.webp': 'image/webp',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.xml': 'application/xml; charset=utf-8',
});

function extractRawPathname(requestTarget) {
  const target = String(requestTarget == null ? '' : requestTarget);
  const query = target.indexOf('?');
  const fragment = target.indexOf('#');
  let end = target.length;
  if (query !== -1) end = Math.min(end, query);
  if (fragment !== -1) end = Math.min(end, fragment);
  return target.slice(0, end);
}

function selectRoutingPathname(rawPathname, normalizedPathname) {
  return rawPathname.startsWith('/workspace') || rawPathname.startsWith('/legacy')
    ? rawPathname
    : normalizedPathname;
}

function inside(root, target) {
  const relative = path.relative(path.resolve(root), path.resolve(target));
  return relative === '' || (
    relative !== '..'
    && !relative.startsWith(`..${path.sep}`)
    && !path.isAbsolute(relative)
  );
}

function realpath(file) {
  const resolve = fs.realpathSync.native || fs.realpathSync;
  return resolve(file);
}

function existingFile(root, file) {
  if (!inside(root, file)) return { state: 'escape' };
  try {
    const canonicalRoot = realpath(root);
    const canonicalFile = realpath(file);
    if (!inside(canonicalRoot, canonicalFile)) return { state: 'escape' };
    const stats = fs.statSync(canonicalFile);
    return stats.isFile()
      ? { state: 'file', file: canonicalFile, size: stats.size }
      : { state: 'missing' };
  } catch (_) {
    return { state: 'missing' };
  }
}

function canonicalRoots(roots) {
  if (!roots || !roots.react || !roots.legacy) {
    throw new TypeError('frontend roots must include react and legacy directories');
  }
  const react = path.resolve(roots.react);
  const legacy = path.resolve(roots.legacy);
  return Object.freeze({
    react,
    reactIndex: path.resolve(roots.reactIndex || path.join(react, 'index.html')),
    legacy,
    legacyIndex: path.resolve(roots.legacyIndex || path.join(legacy, 'index.html')),
  });
}

function readImmutableAssets(reactRoot) {
  const manifestTarget = path.join(reactRoot, '.vite', 'manifest.json');
  const manifest = existingFile(reactRoot, manifestTarget);
  if (manifest.state !== 'file') return Object.freeze([]);

  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(manifest.file, 'utf8'));
  } catch (_) {
    return Object.freeze([]);
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return Object.freeze([]);

  const assets = new Set();
  const add = (value) => {
    if (typeof value !== 'string') return;
    const portable = value.replace(/\\/g, '/');
    if (
      portable.startsWith('/')
      || portable.split('/').some((segment) => segment === '' || segment === '.' || segment === '..')
    ) return;
    const target = safeTarget(reactRoot, portable);
    if (target && existingFile(reactRoot, target).state === 'file') assets.add(portable);
  };

  for (const record of Object.values(parsed)) {
    if (!record || typeof record !== 'object' || Array.isArray(record)) continue;
    add(record.file);
    for (const field of ['css', 'assets']) {
      if (Array.isArray(record[field])) record[field].forEach(add);
    }
  }
  return Object.freeze([...assets]);
}

function createFrontendConfig({ roots, uiEntry, warn = console.warn } = {}) {
  const resolvedRoots = canonicalRoots(roots);
  const missing = uiEntry == null;
  const valid = uiEntry === 'react' || uiEntry === 'legacy';
  const requestedEntry = missing || valid ? (uiEntry || 'react') : 'react';
  if (!missing && !valid) {
    warn(`[frontend] Invalid UI_ENTRY=${JSON.stringify(uiEntry)}; defaulting to react.`);
  }

  const reactAvailable = existingFile(resolvedRoots.react, resolvedRoots.reactIndex).state === 'file';
  if (!reactAvailable) {
    const rootBehavior = requestedEntry === 'react'
      ? '/ falls back to legacy'
      : '/ remains on the requested legacy entry';
    warn(`[frontend] React build is unavailable; /workspace/* will return 503 and ${rootBehavior}. Run the frontend build before restarting.`);
  }

  return Object.freeze({
    requestedEntry,
    rootEntry: requestedEntry === 'react' && !reactAvailable ? 'legacy' : requestedEntry,
    reactAvailable,
    immutableAssets: reactAvailable ? readImmutableAssets(resolvedRoots.react) : Object.freeze([]),
  });
}

function forbidden() {
  return {
    kind: 'forbidden',
    status: 403,
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'no-store',
    },
  };
}

function notFound() {
  return {
    kind: 'not-found',
    status: 404,
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'no-store',
    },
  };
}

function unavailable() {
  return {
    kind: 'unavailable',
    status: 503,
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'no-store',
      'Retry-After': '0',
    },
  };
}

function methodNotAllowed() {
  return {
    kind: 'method-not-allowed',
    status: 405,
    headers: {
      Allow: 'GET, HEAD',
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'no-store',
    },
  };
}

function redirect(location) {
  return {
    kind: 'redirect',
    status: 302,
    location,
    headers: {
      Location: location,
      'Cache-Control': 'no-store',
    },
  };
}

function validateRawPathname(rawPathname) {
  if (typeof rawPathname !== 'string' || !rawPathname.startsWith('/')) return null;
  if (rawPathname.includes('\0') || rawPathname.includes('\\')) return null;
  if (/%(?:2f|5c)/i.test(rawPathname)) return null;

  let decoded;
  try {
    decoded = decodeURIComponent(rawPathname);
  } catch (_) {
    return null;
  }
  if (decoded.includes('\0') || decoded.includes('\\')) return null;

  const rawSegments = rawPathname.split('/');
  const decodedSegments = decoded.split('/');
  if (rawSegments.some((segment) => segment === '.' || segment === '..')) return null;
  if (decodedSegments.some((segment) => segment === '.' || segment === '..')) return null;

  return decoded;
}

function safeTarget(root, relative) {
  if (
    !relative
    || path.isAbsolute(relative)
    || path.win32.isAbsolute(relative)
    || /^[A-Za-z]:/.test(relative)
  ) {
    return relative === '' ? path.resolve(root) : null;
  }
  const target = path.resolve(root, relative);
  return inside(root, target) ? target : null;
}

function contentType(file) {
  return FRONTEND_MIME[path.extname(file).toLowerCase()] || 'application/octet-stream';
}

function fileResult(kind, file, relative, flavor, immutableAssets = []) {
  const html = path.extname(file).toLowerCase() === '.html';
  const portable = relative.replace(/\\/g, '/');
  const headers = {
    'Content-Type': contentType(file),
    'Cache-Control': flavor === 'react' && !html && immutableAssets.includes(portable)
      ? 'public,max-age=31536000,immutable'
      : 'no-cache',
  };
  if (flavor === 'react' && html) headers['Content-Security-Policy'] = REACT_CSP;
  return {
    kind: html ? `${flavor}-html` : kind,
    status: 200,
    file,
    headers,
  };
}

function resolveKnownFile(root, target, relative, kind, flavor, immutableAssets = []) {
  const existing = existingFile(root, target);
  if (existing.state === 'escape') return forbidden();
  if (existing.state === 'missing') return null;
  return fileResult(kind, existing.file, relative, flavor, immutableAssets);
}

function resolveReact(relative, roots, config) {
  if (!config.reactAvailable) return unavailable();

  if (relative !== '') {
    const target = safeTarget(roots.react, relative);
    if (!target) return forbidden();
    const file = resolveKnownFile(
      roots.react,
      target,
      relative,
      'react-file',
      'react',
      config.immutableAssets,
    );
    if (file) return file;
    const portable = relative.replace(/\\/g, '/');
    if (portable === 'assets' || portable.startsWith('assets/')) return notFound();
  }

  const index = resolveKnownFile(
    roots.react,
    roots.reactIndex,
    'index.html',
    'react-file',
    'react',
    config.immutableAssets,
  );
  return index || unavailable();
}

function resolveLegacy(relative, roots) {
  const selected = relative === '' ? 'index.html' : relative;
  const target = relative === '' ? roots.legacyIndex : safeTarget(roots.legacy, selected);
  if (!target) return forbidden();
  return resolveKnownFile(roots.legacy, target, selected, 'legacy-file', 'legacy') || notFound();
}

function resolveFrontendPath(rawPathname, inputRoots, options = 'react') {
  const roots = canonicalRoots(inputRoots);
  const config = typeof options === 'string' ? { rootEntry: options } : (options || {});
  const rootEntry = config.rootEntry === 'legacy' ? 'legacy' : 'react';
  const reactAvailable = typeof config.reactAvailable === 'boolean'
    ? config.reactAvailable
    : existingFile(roots.react, roots.reactIndex).state === 'file';
  const immutableAssets = Array.isArray(config.immutableAssets) ? config.immutableAssets : [];

  const decoded = validateRawPathname(rawPathname);
  if (decoded == null) return forbidden();

  if (
    (decoded.startsWith('/workspace') && decoded !== '/workspace' && !decoded.startsWith('/workspace/'))
    || (decoded.startsWith('/legacy') && decoded !== '/legacy' && !decoded.startsWith('/legacy/'))
  ) {
    return forbidden();
  }

  if (decoded === '/') {
    return rootEntry === 'react' ? redirect('/workspace/') : resolveLegacy('', roots);
  }
  if (decoded === '/workspace') return redirect('/workspace/');
  if (decoded === '/legacy') return redirect('/legacy/');
  if (decoded.startsWith('/workspace/')) {
    return resolveReact(decoded.slice('/workspace/'.length), roots, { reactAvailable, immutableAssets });
  }
  if (decoded.startsWith('/legacy/')) {
    return resolveLegacy(decoded.slice('/legacy/'.length), roots);
  }

  if (
    decoded === '/api' || decoded.startsWith('/api/')
    || decoded === '/pdfbytes'
    || decoded === '/papers' || decoded.startsWith('/papers/')
  ) {
    return notFound();
  }

  return resolveLegacy(decoded.slice(1), roots);
}

function fileFailure(error) {
  const status = error && (error.code === 'EACCES' || error.code === 'EPERM')
    ? 403
    : error && (error.code === 'ENOENT' || error.code === 'ENOTDIR')
      ? 404
      : 500;
  return {
    kind: status === 403 ? 'forbidden' : status === 404 ? 'not-found' : 'file-error',
    status,
    headers: {
      'Content-Type': 'text/plain; charset=utf-8',
      'Cache-Control': 'no-store',
    },
  };
}

function sendTerminalResult(res, result, message) {
  if (res.destroyed || res.writableEnded) return;
  res.writeHead(result.status, result.headers);
  res.end(message);
}

function serveResolvedFile(req, res, result, fileOps) {
  const open = fileOps.open || fs.open;
  const fstat = fileOps.fstat || fs.fstat;
  const close = fileOps.close || fs.close;
  const createReadStream = fileOps.createReadStream || fs.createReadStream;

  open(result.file, 'r', (openError, fd) => {
    if (openError) {
      const failure = fileFailure(openError);
      sendTerminalResult(res, failure, failure.status === 404 ? 'not found' : 'unable to open asset');
      return;
    }
    if (res.destroyed || res.writableEnded) {
      close(fd, () => {});
      return;
    }

    fstat(fd, (statError, stats) => {
      if (statError || !stats || !stats.isFile()) {
        close(fd, () => {});
        const failure = fileFailure(statError || Object.assign(new Error('not a file'), { code: 'ENOENT' }));
        sendTerminalResult(res, failure, failure.status === 404 ? 'not found' : 'unable to read asset');
        return;
      }
      if (res.destroyed || res.writableEnded) {
        close(fd, () => {});
        return;
      }

      const headers = { ...result.headers, 'Content-Length': String(stats.size) };
      if (req.method === 'HEAD') {
        res.writeHead(result.status, headers);
        close(fd, () => res.end());
        return;
      }

      let input;
      try {
        input = createReadStream(result.file, { fd, autoClose: true });
      } catch (error) {
        close(fd, () => {});
        const failure = fileFailure(error);
        sendTerminalResult(res, failure, 'unable to read asset');
        return;
      }
      res.writeHead(result.status, headers);
      pipeline(input, res, (error) => {
        if (error && !res.destroyed) res.destroy(error);
      });
    });
  });
}

function serveFrontendRequest(req, res, rawPathname, roots, config, fileOps = fs) {
  const method = String(req.method || 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') {
    const result = methodNotAllowed();
    sendTerminalResult(res, result, 'method not allowed');
    return result;
  }

  const result = resolveFrontendPath(rawPathname, roots, config);
  if (result.kind === 'redirect') {
    res.writeHead(result.status, result.headers);
    res.end();
    return result;
  }

  if (result.file) {
    serveResolvedFile(req, res, result, fileOps);
    return result;
  }

  const messages = {
    forbidden: 'forbidden',
    'method-not-allowed': 'method not allowed',
    'not-found': 'not found',
    unavailable: 'React workspace unavailable; build frontend and restart the server',
  };
  sendTerminalResult(res, result, messages[result.kind] || 'not found');
  return result;
}

module.exports = {
  FRONTEND_MIME,
  REACT_CSP,
  createFrontendConfig,
  extractRawPathname,
  resolveFrontendPath,
  selectRoutingPathname,
  serveFrontendRequest,
};
