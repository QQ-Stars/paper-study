const fs = require('fs');
const path = require('path');
const { resolveDir } = require('./settings');

const fileExists = (fsImpl, filePath) => {
  try {
    return fsImpl.existsSync(filePath);
  } catch (e) {
    return false;
  }
};

const absoluteFromRoot = (root, filePath) => (
  path.isAbsolute(filePath) ? filePath : path.join(root, filePath)
);

const WINDOWS_RESERVED_NAMES = /^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i;

function sanitizeTitleStem(title, {
  fallback = 'paper',
  maxLength = 160,
} = {}) {
  let stem = String(title || '')
    .replace(/[<>:"/\\|?*\x00-\x1f]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[. ]+$/g, '');
  if (!stem) {
    stem = String(fallback || 'paper')
      .replace(/[<>:"/\\|?*\x00-\x1f]+/g, ' ')
      .replace(/\s+/g, ' ')
      .trim()
      .replace(/[. ]+$/g, '');
  }
  if (!stem) stem = 'paper';
  if (stem.length > maxLength) stem = stem.slice(0, maxLength).trim().replace(/[. ]+$/g, '');
  if (WINDOWS_RESERVED_NAMES.test(stem)) stem = '_' + stem;
  return stem || 'paper';
}

function titlePdfFilename(title, options = {}) {
  return sanitizeTitleStem(title, options) + '.pdf';
}

function samePath(left, right) {
  if (!left || !right) return false;
  const a = path.resolve(left);
  const b = path.resolve(right);
  return process.platform === 'win32' ? a.toLowerCase() === b.toLowerCase() : a === b;
}

function isInsideDir(filePath, dirPath) {
  const relative = path.relative(path.resolve(dirPath), path.resolve(filePath));
  return !!relative && !relative.startsWith('..') && !path.isAbsolute(relative);
}

function uniqueTitlePdfPath(pdfDir, title, {
  id = '',
  sourcePath = '',
  fsImpl = fs,
} = {}) {
  const baseStem = sanitizeTitleStem(title);
  const makePath = (stem) => path.join(pdfDir, stem + '.pdf');
  const first = makePath(baseStem);
  if (!fsImpl.existsSync(first) || samePath(first, sourcePath)) return first;

  const idStem = sanitizeTitleStem(id, { fallback: 'paper', maxLength: 48 });
  let candidate = makePath(`${baseStem} - ${idStem}`);
  if (!fsImpl.existsSync(candidate) || samePath(candidate, sourcePath)) return candidate;

  for (let i = 2; i < 1000; i += 1) {
    candidate = makePath(`${baseStem} - ${idStem}-${i}`);
    if (!fsImpl.existsSync(candidate) || samePath(candidate, sourcePath)) return candidate;
  }
  return makePath(`${baseStem} - ${Date.now().toString(36)}`);
}

function planPdfArchive({
  pdfDir,
  sourcePath,
  title,
  id,
  fsImpl = fs,
}) {
  const targetPath = uniqueTitlePdfPath(pdfDir, title, { id, sourcePath, fsImpl });
  const action = samePath(sourcePath, targetPath)
    ? 'noop'
    : (isInsideDir(sourcePath, pdfDir) ? 'move' : 'copy');
  return { action, sourcePath, targetPath };
}

function archivePdf(plan, { fsImpl = fs } = {}) {
  fsImpl.mkdirSync(path.dirname(plan.targetPath), { recursive: true });
  if (plan.action === 'copy') fsImpl.copyFileSync(plan.sourcePath, plan.targetPath);
  if (plan.action === 'move') fsImpl.renameSync(plan.sourcePath, plan.targetPath);
  return plan.targetPath;
}

function createArtifactLocator({
  root = process.cwd(),
  defaultPdfDir = path.join(root, 'data', 'pdfs'),
  seedPdfDir = path.resolve(root, '..', 'paper'),
  settingsStore = { read: () => ({}) },
  getPdfPath = () => null,
  fsImpl = fs,
} = {}) {
  const configuredPdfDir = () => {
    const settings = settingsStore.read() || {};
    return settings.pdfDir ? resolveDir(root, settings.pdfDir) : null;
  };

  const pdfDirsForOpen = () => {
    const dirs = [];
    const customDir = configuredPdfDir();
    if (customDir) dirs.push(customDir);
    dirs.push(defaultPdfDir, seedPdfDir);
    return dirs;
  };

  const pdfDirsForList = () => {
    const dirs = [defaultPdfDir];
    const customDir = configuredPdfDir();
    if (customDir) dirs.push(customDir);
    dirs.push(seedPdfDir);
    return dirs;
  };

  const resolvePdfById = (id) => {
    try {
      const storedPath = getPdfPath(id);
      if (storedPath) {
        const absolutePath = absoluteFromRoot(root, storedPath);
        if (fileExists(fsImpl, absolutePath)) return absolutePath;
      }
    } catch (e) {}

    for (const dir of pdfDirsForOpen()) {
      const filePath = path.join(dir, id + '.pdf');
      if (fileExists(fsImpl, filePath)) return filePath;
    }
    return null;
  };

  const hasPdfForRow = (row) => {
    if (!row) return false;
    if (row.pdf_path) {
      const absolutePath = absoluteFromRoot(root, row.pdf_path);
      if (fileExists(fsImpl, absolutePath)) return true;
    }
    if (!row.id) return false;
    return pdfDirsForList().some((dir) => fileExists(fsImpl, path.join(dir, row.id + '.pdf')));
  };

  return { resolvePdfById, hasPdfForRow };
}

function scanPdfDirectory(dir, {
  fsImpl = fs,
  maxDepth = 4,
  limit = 2000,
} = {}) {
  if (!dir) return { ok: false, error: '缺少文件夹路径' };
  try {
    if (!fsImpl.existsSync(dir) || !fsImpl.statSync(dir).isDirectory()) {
      return { ok: false, error: '文件夹不存在或不是目录' };
    }

    const files = [];
    const walk = (currentDir, depth) => {
      if (depth > maxDepth || files.length >= limit) return;
      let entries = [];
      try {
        entries = fsImpl.readdirSync(currentDir, { withFileTypes: true });
      } catch (e) {
        return;
      }

      for (const entry of entries) {
        if (files.length >= limit) break;
        const filePath = path.join(currentDir, entry.name);
        if (entry.isDirectory()) {
          if (!entry.name.startsWith('.')) walk(filePath, depth + 1);
        } else if (/\.pdf$/i.test(entry.name)) {
          try {
            files.push({ path: filePath, name: entry.name, size: fsImpl.statSync(filePath).size });
          } catch (e) {}
        }
      }
    };

    walk(dir, 0);
    files.sort((a, b) => a.path.localeCompare(b.path));
    return { ok: true, dir, count: files.length, files };
  } catch (error) {
    return { ok: false, error: String(error && error.message || error) };
  }
}

module.exports = {
  archivePdf,
  createArtifactLocator,
  planPdfArchive,
  scanPdfDirectory,
  sanitizeTitleStem,
  titlePdfFilename,
  uniqueTitlePdfPath,
};
