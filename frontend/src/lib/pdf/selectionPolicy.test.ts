import { applyPdfSelectionPolicy } from './selectionPolicy';

it('locks selection to the starting column and filters small footnotes', () => {
  const result = applyPdfSelectionPolicy({
    pageNumber: 3,
    pageRect: { left: 0, top: 0, right: 1_000, bottom: 1_400 },
    startFragmentIndex: 0,
    nativeText: 'Main claim footnote Wrong column',
    fragments: [
      {
        text: 'Main',
        fontSize: 16,
        rect: { left: 80, top: 200, right: 180, bottom: 220 },
      },
      {
        text: 'claim',
        fontSize: 16,
        breakBefore: 'space',
        rect: { left: 190, top: 200, right: 300, bottom: 220 },
      },
      {
        text: 'footnote',
        fontSize: 9,
        breakBefore: 'line',
        rect: { left: 80, top: 230, right: 170, bottom: 242 },
      },
      {
        text: 'Wrong column',
        fontSize: 16,
        rect: { left: 590, top: 200, right: 800, bottom: 220 },
      },
    ],
  });

  expect(result).toEqual({
    kind: 'accepted',
    text: 'Main claim',
    source: 'geometry',
    anchor: {
      pageNumber: 3,
      x: 0.08,
      y: 200 / 1_400,
      width: 0.22,
      height: 20 / 1_400,
    },
  });
});

it('keeps a cross-column drag inside the endpoint vertical range', () => {
  const result = applyPdfSelectionPolicy({
    pageNumber: 1,
    pageRect: { left: 0, top: 0, right: 1_000, bottom: 1_400 },
    startFragmentIndex: 1,
    endFragmentIndex: 5,
    nativeText: '',
    fragments: [
      { text: 'before', fontSize: 12, rect: { left: 80, top: 100, right: 180, bottom: 120 } },
      { text: 'start', fontSize: 12, rect: { left: 80, top: 200, right: 180, bottom: 220 } },
      { text: 'middle', fontSize: 12, breakBefore: 'line', rect: { left: 80, top: 300, right: 180, bottom: 320 } },
      { text: 'after', fontSize: 12, breakBefore: 'line', rect: { left: 80, top: 500, right: 180, bottom: 520 } },
      { text: 'right before', fontSize: 12, rect: { left: 600, top: 100, right: 760, bottom: 120 } },
      { text: 'right end', fontSize: 12, rect: { left: 600, top: 390, right: 760, bottom: 410 } },
    ],
  });

  expect(result).toMatchObject({
    kind: 'accepted',
    text: 'start middle',
    source: 'geometry',
  });
});

it('merges hyphenated line wraps, hard lines, and paragraph separators', () => {
  const result = applyPdfSelectionPolicy({
    pageNumber: 1,
    pageRect: { left: 0, top: 0, right: 600, bottom: 900 },
    startFragmentIndex: 0,
    nativeText: '',
    fragments: [
      {
        text: 'represen-',
        fontSize: 12,
        rect: { left: 40, top: 80, right: 130, bottom: 96 },
      },
      {
        text: 'tation',
        fontSize: 12,
        breakBefore: 'line',
        rect: { left: 40, top: 100, right: 100, bottom: 116 },
      },
      {
        text: 'supports',
        fontSize: 12,
        breakBefore: 'space',
        rect: { left: 110, top: 100, right: 180, bottom: 116 },
      },
      {
        text: 'evidence',
        fontSize: 12,
        breakBefore: 'line',
        rect: { left: 40, top: 120, right: 110, bottom: 136 },
      },
      {
        text: 'New paragraph',
        fontSize: 12,
        breakBefore: 'paragraph',
        rect: { left: 40, top: 160, right: 180, bottom: 176 },
      },
    ],
  });

  expect(result).toMatchObject({
    kind: 'accepted',
    text: 'representation supports evidence\n\nNew paragraph',
    source: 'geometry',
  });
});

it('uses native text only when geometry is unavailable', () => {
  const result = applyPdfSelectionPolicy({
    pageNumber: 1,
    pageRect: { left: 0, top: 0, right: 0, bottom: 0 },
    startFragmentIndex: 0,
    nativeText: 'represen-\ntation\ncontinues\n\nNext paragraph',
    fragments: [],
  });

  expect(result).toEqual({
    kind: 'accepted',
    text: 'representation continues\n\nNext paragraph',
    source: 'native',
    anchor: null,
  });
});

it('accepts exactly 6000 characters and rejects overflow without truncation', () => {
  const accepted = applyPdfSelectionPolicy({
    pageNumber: 1,
    pageRect: { left: 0, top: 0, right: 0, bottom: 0 },
    startFragmentIndex: 0,
    nativeText: 'a'.repeat(6_000),
    fragments: [],
  });
  const rejected = applyPdfSelectionPolicy({
    pageNumber: 1,
    pageRect: { left: 0, top: 0, right: 0, bottom: 0 },
    startFragmentIndex: 0,
    nativeText: 'a'.repeat(6_001),
    fragments: [],
  });

  expect(accepted).toMatchObject({ kind: 'accepted' });
  if (accepted.kind === 'accepted') expect(accepted.text).toHaveLength(6_000);
  expect(rejected).toEqual({
    kind: 'rejected',
    reason: 'too-long',
    length: 6_001,
    maxCharacters: 6_000,
    anchor: null,
  });
});
