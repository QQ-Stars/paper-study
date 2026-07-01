const path = require('path');
const fs = require('fs');
const { db } = require('../db');
const { archivePdf, planPdfArchive } = require('../lib/artifacts');
const { createSettingsStore, resolveDir } = require('../lib/settings');

const ROOT = path.join(__dirname, '..');
const settings = createSettingsStore({ root: ROOT }).read();
const PDFS_DIR = settings.pdfDir ? resolveDir(ROOT, settings.pdfDir) : path.join(ROOT, 'data', 'pdfs');
const SEED_DIR = path.resolve(ROOT, '..', 'paper');
const dryRun = process.argv.includes('--dry-run');

function existingPdfFor(row) {
  const candidates = [];
  if (row.pdf_path) {
    candidates.push(path.isAbsolute(row.pdf_path) ? row.pdf_path : path.join(ROOT, row.pdf_path));
  }
  candidates.push(path.join(PDFS_DIR, row.id + '.pdf'));
  candidates.push(path.join(SEED_DIR, row.id + '.pdf'));
  for (const candidate of candidates) {
    try {
      if (candidate && fs.existsSync(candidate) && fs.statSync(candidate).isFile()) {
        return candidate;
      }
    } catch (e) {}
  }
  return null;
}

function storedPathFor(filePath) {
  return path.relative(ROOT, filePath);
}

const rows = db.prepare("SELECT id, title, pdf_path FROM papers WHERE COALESCE(TRIM(title),'') != '' ORDER BY id").all();
let renamed = 0;
let copied = 0;
let already = 0;
let missing = 0;
let failed = 0;
const updates = [];

for (const row of rows) {
  const sourcePath = existingPdfFor(row);
  if (!sourcePath) {
    missing += 1;
    continue;
  }
  try {
    const plan = planPdfArchive({
      pdfDir: PDFS_DIR,
      sourcePath,
      title: row.title,
      id: row.id,
    });
    if (plan.action === 'noop') {
      already += 1;
      const stored = storedPathFor(plan.targetPath);
      if (row.pdf_path !== stored) updates.push([stored, row.id]);
      continue;
    }
    if (!dryRun) {
      archivePdf(plan);
      updates.push([storedPathFor(plan.targetPath), row.id]);
    }
    if (plan.action === 'copy') copied += 1;
    if (plan.action === 'move') renamed += 1;
    console.log(`${plan.action.toUpperCase()}::${row.id}::${path.basename(sourcePath)} -> ${path.basename(plan.targetPath)}`);
  } catch (error) {
    failed += 1;
    console.error(`ERR::${row.id}::${error && error.message || error}`);
  }
}

if (!dryRun && updates.length) {
  const stmt = db.prepare("UPDATE papers SET pdf_path = ?, updated_at = datetime('now') WHERE id = ?");
  const tx = db.transaction((items) => {
    for (const item of items) stmt.run(...item);
  });
  tx(updates);
}

const summary = {
  ok: failed === 0,
  dryRun,
  pdfDir: PDFS_DIR,
  renamed,
  copied,
  already,
  missing,
  failed,
  updated: dryRun ? 0 : updates.length,
};
console.log(JSON.stringify(summary, null, 2));
