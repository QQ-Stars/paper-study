import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { LibraryPager } from './LibraryPager';

function renderPager(overrides: Partial<Parameters<typeof LibraryPager>[0]> = {}) {
  const onPageChange = vi.fn();
  const onPageSizeChange = vi.fn();
  const view = render(
    <LibraryPager
      page={1}
      pageCount={3}
      pageSize={30}
      total={65}
      rangeStart={1}
      rangeEnd={30}
      onPageChange={onPageChange}
      onPageSizeChange={onPageSizeChange}
      {...overrides}
    />,
  );
  return { ...view, onPageChange, onPageSizeChange };
}

describe('LibraryPager', () => {
  it('shows the visible range and navigates between pages', async () => {
    const user = userEvent.setup();
    const { onPageChange } = renderPager();

    expect(screen.getByText('第 1–30 条 · 共 65 篇')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled();

    await user.click(screen.getByRole('button', { name: '下一页' }));
    expect(onPageChange).toHaveBeenCalledWith(2);

    await user.click(screen.getByRole('button', { name: '第 3 页' }));
    expect(onPageChange).toHaveBeenCalledWith(3);
  });

  it('collapses long page runs into ellipses around the current page', () => {
    renderPager({ page: 8, pageCount: 12, total: 360, rangeStart: 211, rangeEnd: 240 });

    expect(screen.getByRole('button', { name: '第 1 页' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '第 12 页' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '第 7 页' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '第 8 页' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getByRole('button', { name: '第 9 页' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '第 4 页' })).not.toBeInTheDocument();
    expect(screen.getAllByText('…')).toHaveLength(2);
  });

  it('switches the page size without losing the total summary', async () => {
    const user = userEvent.setup();
    const { onPageSizeChange } = renderPager();

    await user.selectOptions(screen.getByRole('combobox', { name: '每页条数' }), '50');
    expect(onPageSizeChange).toHaveBeenCalledWith(50);
    expect(screen.getByText('第 1–30 条 · 共 65 篇')).toBeInTheDocument();
  });

  it('disables forward navigation on the last page', () => {
    renderPager({ page: 3, pageCount: 3, rangeStart: 61, rangeEnd: 65 });

    expect(screen.getByRole('button', { name: '下一页' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '上一页' })).toBeEnabled();
  });
});
