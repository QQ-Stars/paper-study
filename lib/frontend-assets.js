const fs = require('node:fs');
const path = require('node:path');

const REACT_CSP = [
  "default-src 'self'",
  "script-src 'self'",
  "worker-src 'self'",
  "connect-src 'self'",
  "style-src 'self' 'unsafe-inline'",
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
  '.mjs': 'text/javascript; charset=utf-8',
  '.cjs': 'text/javascript; charset=utf-8',
  '.otf': 'font/otf',
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

function isFile(file) {
  try {
    return fs.statSync(file).isFile();
  } catch (_) {
    return false;
  }
}

function realpath(file) {
  const resolve = fs.realpathSync.native || fs.realpathSync;
  return resolve(file);
}

function existingFileState(root, file) {
  if (!inside(root, file)) return 'escape';
  if (!isFile(file)) return 'missing';
  try {
    return inside(realpath(root), realpath(file)) ? 'file' : 'escape';
  } catch (_) {
    return 'missing';
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

function createFrontendConfig({ roots, uiEntry, warn = console.warn } = {}) {
  const resolvedRoots = canonicalRoots(roots);
  const missing = uiEntry == null;
  const valid = uiEntry === 'react' || uiEntry === 'legacy';
  const requestedEntry = missing || valid ? (uiEntry || 'react') : 'react';
  if (!missing && !valid) {
    warn(`[frontend] Invalid UI_ENTRY=${JSON.stringify(uiEntry)}; defaulting to react.`);
  }

  const reactAvailable = existingFileState(resolvedRoots.react, resolvedRoots.reactIndex) === 'file';
  if (!reactAvailable) {
    warn('[frontend] React build is unavailable; /workspace/* will return 503 and / falls back to legacy. Run the frontend build before restarting.');
  }

  return Object.freeze({
    requestedEntry,
    rootEntry: requestedEntry === 'react' && !reactAvailable ? 'legacy' : requestedEntry,
    reactAvailable,
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

function isViteHashedAsset(relative) {
  const portable = relative.replace(/\\/g, '/');
  if (!portable.startsWith('assets/')) return false;
  return /-[A-Za-z0-9_-]{8,}(?:\.[^./]+)+$/.test(path.posix.basename(portable));
}

function fileResult(kind, file, relative, flavor) {
  const html = path.extname(file).toLowerCase() === '.html';
  const headers = {
    'Content-Type': contentType(file),
    'Cache-Control': flavor === 'react' && !html && isViteHashedAsset(relative)
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

function resolveKnownFile(root, target, relative, kind, flavor) {
  const state = existingFileState(root, target);
  if (state === 'escape') return forbidden();
  if (state === 'missing') return null;
  return fileResult(kind, target, relative, flavor);
}

function resolveReact(relative, roots, reactAvailable) {
  if (!reactAvailable) return unavailable();

  if (relative !== '') {
    const target = safeTarget(roots.react, relative);
    if (!target) return forbidden();
    const file = resolveKnownFile(roots.react, target, relative, 'react-file', 'react');
    if (file) return file;
    const portable = relative.replace(/\\/g, '/');
    if (portable === 'assets' || portable.startsWith('assets/')) return notFound();
  }

  const index = resolveKnownFile(roots.react, roots.reactIndex, 'index.html', 'react-file', 'react');
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
    : existingFileState(roots.react, roots.reactIndex) === 'file';

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
    return resolveReact(decoded.slice('/workspace/'.length), roots, reactAvailable);
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

function serveFrontendRequest(req, res, rawPathname, roots, config) {
  const result = resolveFrontendPath(rawPathname, roots, config);
  if (result.kind === 'redirect') {
    res.writeHead(result.status, result.headers);
    res.end();
    return result;
  }

  if (result.file) {
    res.writeHead(result.status, result.headers);
    if (req.method === 'HEAD') res.end();
    else fs.createReadStream(result.file).pipe(res);
    return result;
  }

  const messages = {
    forbidden: 'forbidden',
    'not-found': 'not found',
    unavailable: 'React workspace unavailable; build frontend and restart the server',
  };
  res.writeHead(result.status, result.headers);
  res.end(messages[result.kind] || 'not found');
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
