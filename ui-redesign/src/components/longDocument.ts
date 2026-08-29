export const LONG_DOCUMENT_THRESHOLD = 20_000;
export const LONG_DOCUMENT_PAGE_TARGET = 10_000;
export const LONG_DOCUMENT_FORMULA_TARGET = 70;

function countUnescapedDoubleDollars(line: string): number {
  let count = 0;
  for (let index = 0; index < line.length - 1; index += 1) {
    if (line[index] !== '$' || line[index + 1] !== '$') continue;
    let slashCount = 0;
    for (let cursor = index - 1; cursor >= 0 && line[cursor] === '\\'; cursor -= 1) {
      slashCount += 1;
    }
    if (slashCount % 2 === 0) count += 1;
    index += 1;
  }
  return count;
}

function countInlineFormulas(line: string): number {
  let singleDollarCount = 0;
  for (let index = 0; index < line.length; index += 1) {
    if (line[index] !== '$') continue;
    let slashCount = 0;
    for (let cursor = index - 1; cursor >= 0 && line[cursor] === '\\'; cursor -= 1) {
      slashCount += 1;
    }
    if (slashCount % 2 !== 0) continue;
    if (line[index + 1] === '$') {
      index += 1;
      continue;
    }
    singleDollarCount += 1;
  }
  return Math.floor(singleDollarCount / 2) + (line.match(/\\\(/g)?.length ?? 0);
}

function countSequence(line: string, sequence: string): number {
  let count = 0;
  let cursor = 0;
  while (cursor < line.length) {
    const index = line.indexOf(sequence, cursor);
    if (index < 0) break;
    count += 1;
    cursor = index + sequence.length;
  }
  return count;
}

function fenceStart(line: string): { character: '`' | '~'; length: number } | null {
  const match = line.match(/^\s*(`{3,}|~{3,})/);
  if (!match) return null;
  return {
    character: match[1][0] as '`' | '~',
    length: match[1].length,
  };
}

function closesFence(
  line: string,
  fence: { character: '`' | '~'; length: number },
): boolean {
  const marker = fence.character.repeat(fence.length);
  return new RegExp(`^\\s*${marker}\\s*$`).test(line);
}

/**
 * Splits long Markdown at structural boundaries while preserving the original
 * bytes. Fenced code and display-math blocks stay on one page so rendering a
 * page never exposes a broken Markdown construct. Formula-heavy text also
 * paginates early because KaTeX expands each expression into many DOM nodes.
 */
export function paginateMarkdown(
  source: string,
  targetSize = LONG_DOCUMENT_PAGE_TARGET,
): string[] {
  const safeTarget = Math.max(2_000, Math.floor(targetSize));
  const paginateByLength = source.length > LONG_DOCUMENT_THRESHOLD;
  const pages: string[] = [];
  let pageStart = 0;
  let offset = 0;
  let fence: { character: '`' | '~'; length: number } | null = null;
  let mathBlock: '$$' | '\\[' | null = null;
  let pageFormulaCount = 0;

  const flushAt = (boundary: number) => {
    if (boundary <= pageStart) return;
    pages.push(source.slice(pageStart, boundary));
    pageStart = boundary;
    pageFormulaCount = 0;
  };

  const shouldFlush = (boundary: number, formulaCount = pageFormulaCount) =>
    (paginateByLength && boundary - pageStart >= safeTarget) ||
    formulaCount >= LONG_DOCUMENT_FORMULA_TARGET;

  const lines = source.match(/[^\r\n]*(?:\r\n|\r|\n|$)/g) ?? [source];
  for (const rawLine of lines) {
    if (!rawLine) continue;
    const lineStart = offset;
    const lineEnd = offset + rawLine.length;
    const line = rawLine.replace(/(?:\r\n|\r|\n)$/, '');
    const trimmed = line.trim();
    let closedProtectedBlock = false;

    if (
      !fence &&
      !mathBlock &&
      /^#{1,6}\s/.test(trimmed) &&
      lineStart > pageStart &&
      shouldFlush(lineStart)
    ) {
      flushAt(lineStart);
    }

    if (fence) {
      if (closesFence(line, fence)) {
        fence = null;
        closedProtectedBlock = true;
      }
    } else if (mathBlock === '$$') {
      if (countUnescapedDoubleDollars(line) % 2 === 1) {
        mathBlock = null;
        pageFormulaCount += 1;
        closedProtectedBlock = true;
      }
    } else if (mathBlock === '\\[') {
      if (line.includes('\\]')) {
        mathBlock = null;
        pageFormulaCount += 1;
        closedProtectedBlock = true;
      }
    } else {
      const openingFence = fenceStart(line);
      if (openingFence) {
        fence = openingFence;
      } else {
        const doubleDollars = countUnescapedDoubleDollars(line);
        const bracketOpens = countSequence(line, '\\[');
        const bracketCloses = countSequence(line, '\\]');
        pageFormulaCount += countInlineFormulas(line) + Math.floor(doubleDollars / 2);
        pageFormulaCount += Math.min(bracketOpens, bracketCloses);
        if (doubleDollars % 2 === 1) mathBlock = '$$';
        else if (bracketOpens > bracketCloses) mathBlock = '\\[';
      }
    }

    if (!fence && !mathBlock && (trimmed === '' || closedProtectedBlock)) {
      if (shouldFlush(lineEnd)) flushAt(lineEnd);
    }

    offset = lineEnd;
  }

  flushAt(source.length);
  return pages.length > 0 ? pages : [source];
}
