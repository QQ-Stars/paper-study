import { render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

import { WorkspaceSlotBoundary } from './WorkspaceSlotBoundary';

let consoleError: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined);
});

afterEach(() => {
  consoleError.mockRestore();
});

function BrokenSlot(): never {
  throw new Error('slot failed');
}

it('contains a slot failure without replacing sibling workspace content', () => {
  render(
    <div>
      <main>仍可阅读论文</main>
      <WorkspaceSlotBoundary label="论文上下文">
        <BrokenSlot />
      </WorkspaceSlotBoundary>
    </div>,
  );

  expect(screen.getByRole('main')).toHaveTextContent('仍可阅读论文');
  expect(screen.getByRole('alert')).toHaveTextContent('论文上下文暂时不可用');
  expect(consoleError).toHaveBeenCalled();
});
