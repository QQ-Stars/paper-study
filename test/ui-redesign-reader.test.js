const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const readerPath = path.join(root, 'ui-redesign', 'src', 'components', 'ReaderPage.tsx');
const readerSource = fs.readFileSync(readerPath, 'utf8').replace(/\r\n/g, '\n');

function pdfBranch() {
  const start = readerSource.indexOf("{tab === 'pdf' &&");
  const end = readerSource.indexOf(
    '\n          </div>\n        </SelectionTranslate>',
    start,
  );
  assert.ok(start >= 0 && end > start, 'ReaderPage should have a PDF tab render branch');
  return readerSource.slice(start, end);
}

test('PDF 阅读 tab does not render the OCR Markdown document above the PDF', () => {
  assert.doesNotMatch(pdfBranch(), /<MarkdownView\s+source=\{ocr\.markdown\}/);
  assert.doesNotMatch(pdfBranch(), /className="ocr-panel/);
});

test('PDF 阅读 tab keeps the PDF viewer surface', () => {
  assert.match(pdfBranch(), /<PdfViewer\b/);
});
