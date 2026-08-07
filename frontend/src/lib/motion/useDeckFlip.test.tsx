import {
  useLayoutEffect,
  useRef,
  type RefObject,
} from 'react';

import { cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { useDeckFlip } from './useDeckFlip';

type UseGsapCallback = () => void | (() => void);
interface UseGsapConfig {
  readonly dependencies?: readonly unknown[];
  readonly scope?: RefObject<HTMLElement | null>;
  readonly revertOnUpdate?: boolean;
}

const motion = vi.hoisted(() => ({
  reduceMotion: false,
  configs: [] as UseGsapConfig[],
  getState: vi.fn(),
  flipFrom: vi.fn(),
  killFlipsOf: vi.fn(),
  killTweensOf: vi.fn(),
  set: vi.fn(),
  timelineFromTo: vi.fn(),
  matchMediaAdd: vi.fn(),
  matchMediaRevert: vi.fn(),
  useGSAP: vi.fn(),
}));

const originalMatchMedia = window.matchMedia;

vi.mock('./gsap', () => ({
  Flip: {
    getState: motion.getState,
    from: motion.flipFrom,
    killFlipsOf: motion.killFlipsOf,
  },
  gsap: {
    killTweensOf: motion.killTweensOf,
    set: motion.set,
    timeline: vi.fn(() => ({
      fromTo: motion.timelineFromTo,
    })),
    matchMedia: vi.fn(() => ({
      add: (
        conditions: unknown,
        callback: (context: {
          conditions: { reduceMotion: boolean; allowMotion: boolean };
        }) => void,
      ) => {
        motion.matchMediaAdd(conditions);
        callback({
          conditions: {
            reduceMotion: motion.reduceMotion,
            allowMotion: !motion.reduceMotion,
          },
        });
      },
      revert: motion.matchMediaRevert,
    })),
  },
  useGSAP: motion.useGSAP,
}));

function useGsapMock(callback: UseGsapCallback, config: UseGsapConfig) {
  motion.configs.push(config);
  useLayoutEffect(
    () => callback(),
    [callback, config],
  );
}

function Deck({
  layoutKey,
  offset,
}: {
  readonly layoutKey: string;
  readonly offset?: number;
}) {
  const scope = useRef<HTMLDivElement>(null);
  useDeckFlip({ scope, layoutKey });

  return (
    <div ref={scope}>
      <div
        data-deck-card=""
        data-layout={layoutKey}
        data-layout-offset={offset}
      />
    </div>
  );
}

beforeEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn((query: string) => ({
      matches: query === '(prefers-reduced-motion: reduce)'
        ? motion.reduceMotion
        : !motion.reduceMotion,
    })),
  });
  motion.reduceMotion = false;
  motion.configs.length = 0;
  motion.getState.mockReset();
  motion.getState.mockImplementation(() => ({ id: Symbol('state') }));
  motion.flipFrom.mockReset();
  motion.killFlipsOf.mockReset();
  motion.killTweensOf.mockReset();
  motion.set.mockReset();
  motion.timelineFromTo.mockReset();
  motion.timelineFromTo.mockReturnThis();
  motion.matchMediaAdd.mockReset();
  motion.matchMediaRevert.mockReset();
  motion.useGSAP.mockReset();
  motion.useGSAP.mockImplementation(useGsapMock);
});

afterEach(() => {
  cleanup();
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: originalMatchMedia,
  });
});

describe('useDeckFlip', () => {
  it('uses scoped FLIP geometry for committed layout changes', () => {
    const view = render(<Deck layoutKey="one" offset={1} />);

    expect(motion.configs[0]).toEqual(expect.objectContaining({
      revertOnUpdate: true,
      scope: expect.objectContaining({ current: expect.any(HTMLElement) }),
    }));
    expect(motion.matchMediaAdd).toHaveBeenCalledWith({
      reduceMotion: '(prefers-reduced-motion: reduce)',
      allowMotion: '(prefers-reduced-motion: no-preference)',
    });
    expect(motion.timelineFromTo).toHaveBeenCalledOnce();
    expect(motion.flipFrom).not.toHaveBeenCalled();
    expect(motion.getState).toHaveBeenCalledOnce();

    view.rerender(<Deck layoutKey="two" offset={0} />);

    expect(motion.timelineFromTo).toHaveBeenCalledOnce();
    expect(motion.flipFrom).toHaveBeenCalledOnce();
    expect(motion.flipFrom.mock.calls[0][1]).toEqual(expect.objectContaining({
      duration: 0.32,
      ease: 'power3.out',
      fade: true,
      scale: true,
      simple: true,
      clearProps: 'transform,opacity,visibility',
      onComplete: expect.any(Function),
      onInterrupt: expect.any(Function),
    }));
    expect(motion.flipFrom.mock.calls[0][1].duration).toBeGreaterThanOrEqual(0.28);
    expect(motion.flipFrom.mock.calls[0][1].duration).toBeLessThanOrEqual(0.42);
    expect(motion.matchMediaRevert).toHaveBeenCalled();

    view.rerender(<Deck layoutKey="three" offset={-1} />);

    expect(motion.timelineFromTo).toHaveBeenCalledOnce();
    expect(motion.flipFrom).toHaveBeenCalledTimes(2);
  });

  it('commits the static final presentation when reduced motion is requested', () => {
    motion.reduceMotion = true;

    render(<Deck layoutKey="reduced" />);

    expect(motion.set).not.toHaveBeenCalled();
    expect(motion.timelineFromTo).not.toHaveBeenCalled();
    expect(motion.flipFrom).not.toHaveBeenCalled();
    expect(motion.getState).not.toHaveBeenCalled();
    expect(motion.matchMediaAdd).not.toHaveBeenCalled();
  });

  it('uses the static presentation when matchMedia is unavailable', () => {
    Object.defineProperty(window, 'matchMedia', {
      configurable: true,
      value: undefined,
    });

    render(<Deck layoutKey="no-match-media" />);

    expect(motion.matchMediaAdd).not.toHaveBeenCalled();
    expect(motion.set).not.toHaveBeenCalled();
    expect(motion.timelineFromTo).not.toHaveBeenCalled();
    expect(motion.flipFrom).not.toHaveBeenCalled();
    expect(motion.getState).not.toHaveBeenCalled();
  });
});
