import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import {
  DashboardView,
  handle,
  type DashboardPaper,
} from './DashboardRoute';

const paper = (id: string, title: string): DashboardPaper => ({
  id,
  title,
  venue: 'CSCW',
  year: '2026',
  status: '学习中',
  createdAt: `2026-08-0${id}T08:00:00.000Z`,
});

describe('DashboardView', () => {
  it('declares the inspector and timeline workspace layout', () => {
    expect(handle).toEqual(expect.objectContaining({
      title: '研究概览',
      layout: 'inspector-timeline',
    }));
  });

  it('keeps the deck and inspector on one reducer-owned selection', async () => {
    const user = userEvent.setup();
    const onSelectionChange = vi.fn();
    const papers = [paper('1', 'Paper One'), paper('2', 'Paper Two')];

    const view = render(
      <DashboardView
        papers={papers}
        reviews={[]}
        jobs={[]}
        preferredPaperId="2"
        inspectorMode="rail"
        onSelectionChange={onSelectionChange}
        onOpenPaper={vi.fn()}
      />,
    );

    expect(screen.getByRole('option', { name: /Paper Two/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    expect(screen.getByRole('complementary', { name: '论文上下文' })).toHaveTextContent('Paper Two');

    await user.click(screen.getByRole('option', { name: /Paper One/ }));
    expect(screen.getByRole('complementary', { name: '论文上下文' })).toHaveTextContent('Paper One');
    expect(onSelectionChange).toHaveBeenLastCalledWith('1');

    view.rerender(
      <DashboardView
        papers={[papers[1]!]}
        reviews={[]}
        jobs={[]}
        preferredPaperId="1"
        inspectorMode="rail"
        onSelectionChange={onSelectionChange}
        onOpenPaper={vi.fn()}
      />,
    );

    await waitFor(() => {
      expect(screen.getByRole('option', { name: /Paper Two/ })).toHaveAttribute(
        'aria-selected',
        'true',
      );
    });
  });

  it('shows distinct loading and recoverable error states without sample papers', async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    const view = render(
      <DashboardView
        papers={[]}
        reviews={[]}
        jobs={[]}
        status="pending"
        inspectorMode="rail"
        onSelectionChange={vi.fn()}
        onOpenPaper={vi.fn()}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('正在载入真实论文');
    expect(screen.queryByRole('option')).not.toBeInTheDocument();

    view.rerender(
      <DashboardView
        papers={[]}
        reviews={[]}
        jobs={[]}
        status="error"
        errorMessage="论文列表暂时不可用"
        onRetry={onRetry}
        inspectorMode="rail"
        onSelectionChange={vi.fn()}
        onOpenPaper={vi.fn()}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('论文列表暂时不可用');
    await user.click(screen.getByRole('button', { name: '重试载入概览' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('opens and closes the responsive paper context without changing selection', async () => {
    const user = userEvent.setup();
    const selected = paper('1', 'Paper One');

    render(
      <DashboardView
        papers={[selected]}
        reviews={[]}
        jobs={[]}
        inspectorMode="drawer"
        onSelectionChange={vi.fn()}
        onOpenPaper={vi.fn()}
      />,
    );

    expect(screen.queryByRole('dialog', { name: '论文上下文' })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '显示论文上下文' }));
    expect(screen.getByRole('dialog', { name: '论文上下文' })).toHaveTextContent('Paper One');
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog', { name: '论文上下文' })).not.toBeInTheDocument();
    expect(screen.getByRole('option', { name: /Paper One/ })).toHaveAttribute('aria-selected', 'true');
  });
});
