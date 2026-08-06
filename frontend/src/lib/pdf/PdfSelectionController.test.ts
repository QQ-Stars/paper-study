import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { PdfSelectionPolicyInput } from './selectionPolicy';
import { PdfSelectionController } from './PdfSelectionController';

function policyInput(text: string): PdfSelectionPolicyInput {
  return {
    pageNumber: 2,
    pageRect: { left: 0, top: 0, right: 0, bottom: 0 },
    fragments: [],
    startFragmentIndex: 0,
    nativeText: text,
  };
}

function selectionFor(root: HTMLElement) {
  return {
    anchorNode: root,
    focusNode: root,
    isCollapsed: false,
    rangeCount: 0,
    removeAllRanges: vi.fn(),
    toString: () => 'native selection',
  } as unknown as Selection;
}

describe('PdfSelectionController', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('replaces by default and appends with Alt or an explicit continuation', () => {
    const root = document.createElement('div');
    document.body.append(root);
    const selection = selectionFor(root);
    const resolveSelection = vi
      .fn()
      .mockReturnValueOnce(policyInput('first fragment'))
      .mockReturnValueOnce(policyInput('second fragment'))
      .mockReturnValueOnce(policyInput('third fragment'));
    const controller = new PdfSelectionController({
      getSelection: () => selection,
      resolveSelection,
      delayMs: 10,
    });
    const cleanup = controller.mount(root);
    controller.switchPaper('paper-a', 1);

    root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    vi.runAllTimers();
    expect(controller.getSnapshot()).toMatchObject({
      status: 'selected',
      text: 'first fragment',
      fragments: [{ text: 'first fragment' }],
      popoverOpen: true,
    });

    root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, altKey: true }));
    vi.runAllTimers();
    expect(controller.getSnapshot().text).toBe('first fragment\n\nsecond fragment');

    controller.beginContinuation();
    expect(controller.getSnapshot()).toMatchObject({
      text: 'first fragment\n\nsecond fragment',
      popoverOpen: false,
    });
    root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    vi.runAllTimers();
    expect(controller.getSnapshot().text).toBe(
      'first fragment\n\nsecond fragment\n\nthird fragment',
    );

    cleanup();
  });

  it('keeps the buffer on zoom/outside click but clears everything on paper switch', () => {
    const root = document.createElement('div');
    const outside = document.createElement('button');
    const popover = document.createElement('aside');
    document.body.append(root, outside, popover);
    const selection = selectionFor(root);
    const controller = new PdfSelectionController({
      getSelection: () => selection,
      resolveSelection: () => policyInput('kept fragment'),
      delayMs: 1,
    });
    controller.mount(root);
    controller.switchPaper('paper-a', 1);
    controller.setPopoverElement(popover);
    root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    vi.runAllTimers();

    outside.dispatchEvent(new Event('pointerdown', { bubbles: true }));
    expect(controller.getSnapshot()).toMatchObject({
      text: 'kept fragment',
      popoverOpen: false,
    });
    root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    vi.runAllTimers();
    controller.onZoom();
    expect(controller.getSnapshot()).toMatchObject({
      text: 'kept fragment',
      popoverOpen: false,
    });
    expect(selection.removeAllRanges).toHaveBeenCalled();

    controller.switchPaper('paper-b', 2);
    expect(controller.getSnapshot()).toMatchObject({
      status: 'idle',
      text: '',
      fragments: [],
      popoverOpen: false,
    });
    controller.dispose();
  });

  it('rejects an overflowing combined buffer without truncating the prior text', () => {
    const root = document.createElement('div');
    document.body.append(root);
    const controller = new PdfSelectionController({
      getSelection: () => selectionFor(root),
      resolveSelection: vi
        .fn()
        .mockReturnValueOnce(policyInput('a'.repeat(5_999)))
        .mockReturnValueOnce(policyInput('bc')),
      delayMs: 1,
    });
    controller.mount(root);
    controller.switchPaper('paper-a', 1);
    root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    vi.runAllTimers();
    root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, altKey: true }));
    vi.runAllTimers();

    const snapshot = controller.getSnapshot();
    expect(snapshot).toMatchObject({
      status: 'error',
      error: { reason: 'too-long', length: 6_003, maxCharacters: 6_000 },
    });
    expect(snapshot.text).toHaveLength(5_999);
  });

  it('does not leak a rejected Alt append into the next ordinary selection', () => {
    const root = document.createElement('div');
    document.body.append(root);
    const controller = new PdfSelectionController({
      getSelection: () => selectionFor(root),
      resolveSelection: vi
        .fn()
        .mockReturnValueOnce(policyInput('base'))
        .mockReturnValueOnce(policyInput('x'.repeat(11)))
        .mockReturnValueOnce(policyInput('new')),
      delayMs: 1,
      maxCharacters: 10,
    });
    controller.mount(root);
    controller.switchPaper('paper-a', 1);
    root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    vi.runAllTimers();
    root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, altKey: true }));
    vi.runAllTimers();
    expect(controller.getSnapshot().status).toBe('error');

    root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    vi.runAllTimers();

    expect(controller.getSnapshot()).toMatchObject({
      status: 'selected',
      text: 'new',
      fragments: [{ text: 'new' }],
    });
  });

  it('remounts without duplicate listeners and final cleanup cancels pending work', () => {
    const root = document.createElement('div');
    document.body.append(root);
    const resolveSelection = vi.fn(() => policyInput('strict fragment'));
    const controller = new PdfSelectionController({
      getSelection: () => selectionFor(root),
      resolveSelection,
      delayMs: 20,
    });

    const firstCleanup = controller.mount(root);
    firstCleanup();
    firstCleanup();
    const secondCleanup = controller.mount(root);
    root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    vi.runAllTimers();
    expect(resolveSelection).toHaveBeenCalledOnce();

    root.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
    secondCleanup();
    vi.runAllTimers();
    expect(resolveSelection).toHaveBeenCalledOnce();
  });
});
