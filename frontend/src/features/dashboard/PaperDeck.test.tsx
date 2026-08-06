import { useReducer } from 'react';

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { PaperDeck, type PaperDeckItem } from './PaperDeck';
import { deckReducer, reconcile } from './deckReducer';

const makePaper = (id: string): PaperDeckItem => ({
  id,
  title: `Paper ${id}`,
  venue: 'CHI',
  year: '2026',
  status: '学习中',
});

function Harness({
  papers,
  preferredId,
  onOpen,
}: {
  readonly papers: readonly PaperDeckItem[];
  readonly preferredId?: string;
  readonly onOpen: (paperId: string) => void;
}) {
  const [state, dispatch] = useReducer(
    deckReducer,
    reconcile(papers, preferredId),
  );

  return (
    <PaperDeck
      papers={papers}
      state={state}
      onSelect={(paperId) => dispatch({ type: 'paper-selected', paperId })}
      onMove={(delta) => dispatch({ type: 'selection-moved', delta })}
      onOpen={onOpen}
    />
  );
}

describe('PaperDeck', () => {
  it('exposes a five-card listbox while reporting the exact result count', () => {
    const items = ['1', '2', '3', '4', '5', '6', '7'].map(makePaper);

    render(<Harness papers={items} preferredId="3" onOpen={vi.fn()} />);

    expect(screen.getByRole('listbox', { name: '论文甲板' })).toBeInTheDocument();
    expect(screen.getAllByRole('option')).toHaveLength(5);
    expect(screen.getByText('共 7 篇')).toBeInTheDocument();
    expect(screen.getByText('03 / 07')).toBeInTheDocument();
    expect(screen.getByRole('option', { name: /Paper 3/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(
      screen.getAllByRole('option').map((option) => option.getAttribute('data-offset')),
    ).toEqual(['-2', '-1', '0', '1', '2']);
    expect(
      screen.getAllByRole('option').map((option) => option.getAttribute('data-layout-offset')),
    ).toEqual(['-2', '-1', '0', '1', '2']);
  });

  it('keeps the selected card centered across both deck boundaries', () => {
    const items = ['1', '2', '3', '4', '5', '6', '7'].map(makePaper);
    const firstView = render(
      <Harness papers={items} preferredId="1" onOpen={vi.fn()} />,
    );

    expect(
      screen.getAllByRole('option').map((option) => option.getAttribute('data-offset')),
    ).toEqual(['0', '1', '2', '3', '4']);
    expect(
      screen.getAllByRole('option').map((option) => option.getAttribute('data-layout-offset')),
    ).toEqual(['0', '1', '2', '-2', '-1']);
    expect(screen.getByRole('option', { name: /Paper 1/ })).toHaveAttribute(
      'data-layout-offset',
      '0',
    );

    firstView.unmount();
    render(<Harness papers={items} preferredId="7" onOpen={vi.fn()} />);

    expect(
      screen.getAllByRole('option').map((option) => option.getAttribute('data-offset')),
    ).toEqual(['-4', '-3', '-2', '-1', '0']);
    expect(
      screen.getAllByRole('option').map((option) => option.getAttribute('data-layout-offset')),
    ).toEqual(['1', '2', '-2', '-1', '0']);
    expect(screen.getByRole('option', { name: /Paper 7/ })).toHaveAttribute(
      'data-layout-offset',
      '0',
    );
  });

  it('uses a pointer click only to select without stealing focus, then opens with Enter or double-click', async () => {
    const user = userEvent.setup();
    const onOpen = vi.fn();

    render(
      <>
        <label>
          搜索论文
          <input />
        </label>
        <Harness
          papers={['1', '2', '3'].map(makePaper)}
          preferredId="1"
          onOpen={onOpen}
        />
      </>,
    );

    const search = screen.getByRole('textbox', { name: '搜索论文' });
    const second = screen.getByRole('option', { name: /Paper 2/ });
    search.focus();
    await user.click(second);
    expect(second).toHaveAttribute('aria-selected', 'true');
    expect(search).toHaveFocus();
    expect(onOpen).not.toHaveBeenCalled();

    second.focus();
    await user.keyboard('{Enter}');
    expect(onOpen).toHaveBeenLastCalledWith('2');

    const third = screen.getByRole('option', { name: /Paper 3/ });
    await user.dblClick(third);
    expect(onOpen).toHaveBeenLastCalledWith('3');
  });

  it('moves focus and selection with arrow keys without wrapping at either end', async () => {
    const user = userEvent.setup();

    render(
      <Harness
        papers={['1', '2', '3'].map(makePaper)}
        preferredId="1"
        onOpen={vi.fn()}
      />,
    );

    const first = screen.getByRole('option', { name: /Paper 1/ });
    first.focus();
    await user.keyboard('{ArrowLeft}');
    expect(first).toHaveFocus();
    expect(first).toHaveAttribute('aria-selected', 'true');

    await user.keyboard('{ArrowRight}');
    const second = screen.getByRole('option', { name: /Paper 2/ });
    expect(second).toHaveFocus();
    expect(second).toHaveAttribute('aria-selected', 'true');

    await user.keyboard('{ArrowRight}{ArrowRight}');
    const third = screen.getByRole('option', { name: /Paper 3/ });
    expect(third).toHaveFocus();
    expect(third).toHaveAttribute('aria-selected', 'true');
  });

  it('disables explicit movement actions at the current boundary', async () => {
    const user = userEvent.setup();

    render(
      <Harness
        papers={['1', '2'].map(makePaper)}
        preferredId="1"
        onOpen={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: '上一篇论文' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: '下一篇论文' }));
    expect(screen.getByRole('button', { name: '下一篇论文' })).toBeDisabled();
  });
});
