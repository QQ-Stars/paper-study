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

it('keeps one entrance presentation live after the StrictMode probe', () => {
  const view = render(
    <StrictMode>
      <RealDeck layoutKey="initial" />
    </StrictMode>,
  );

  const initialCard = view.getByText('initial');
  expect(initialCard).toBeInTheDocument();
  expect(initialCard.style.opacity).not.toBe('');
  expect(initialCard.style.transform).not.toBe('');

  view.rerender(
    <StrictMode>
      <RealDeck layoutKey="updated" />
    </StrictMode>,
  );

  const updatedCard = view.getByRole('button', { name: 'updated' });
  expect(updatedCard.style.opacity).toBe('');
  expect(updatedCard.style.transform).toBe('');

  view.rerender(
    <StrictMode>
      <RealDeck layoutKey="initial" />
    </StrictMode>,
  );

  const reversedCard = view.getByRole('button', { name: 'initial' });
  expect(reversedCard.style.opacity).toBe('');
  expect(reversedCard.style.transform).toBe('');
  expect(() => view.unmount()).not.toThrow();
});
