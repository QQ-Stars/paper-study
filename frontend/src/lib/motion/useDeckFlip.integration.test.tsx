import { StrictMode, useRef } from 'react';

import { render } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { useDeckFlip } from './useDeckFlip';

const originalMatchMedia = window.matchMedia;

function animatedMatchMedia(query: string): MediaQueryList {
  const matches = query === '(prefers-reduced-motion: no-preference)';
  return {
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(() => true),
  };
}

function RealDeck({ layoutKey }: { readonly layoutKey: string }) {
  const scope = useRef<HTMLDivElement>(null);
  useDeckFlip({ scope, layoutKey });

  return (
    <div ref={scope}>
      <button type="button" data-deck-card="">
        {layoutKey}
      </button>
    </div>
  );
}

beforeEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: vi.fn(animatedMatchMedia),
  });
});

afterEach(() => {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    value: originalMatchMedia,
  });
});

it('reverts real GSAP media contexts across StrictMode updates and unmount', () => {
  const view = render(
    <StrictMode>
      <RealDeck layoutKey="initial" />
    </StrictMode>,
  );

  expect(view.getByRole('button', { name: 'initial' })).toBeInTheDocument();

  view.rerender(
    <StrictMode>
      <RealDeck layoutKey="updated" />
    </StrictMode>,
  );

  expect(view.getByRole('button', { name: 'updated' })).toBeInTheDocument();
  expect(() => view.unmount()).not.toThrow();
});
