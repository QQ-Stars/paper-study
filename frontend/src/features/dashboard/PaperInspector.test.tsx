import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { PaperInspector, type PaperInspectorPaper } from './PaperInspector';

const inspectedPaper: PaperInspectorPaper = {
  id: 'paper-a',
  title: 'Evidence-bound Interfaces',
  titleZh: '证据约束界面',
  venue: 'UIST',
  year: '2026',
  type: 'Research',
  topic: 'Human-AI Interaction',
  status: '学习中',
  hasNote: true,
  tldr: 'A verified summary returned with this paper.',
};

describe('PaperInspector', () => {
  it('shows only metadata and review facts supplied for the selected paper', () => {
    render(
      <PaperInspector
        paper={inspectedPaper}
        review={{
          paperId: 'paper-a',
          dueAt: '2026-08-12T08:00:00.000Z',
          currentStep: 2,
          totalSteps: 7,
        }}
        mode="rail"
        open
        onClose={vi.fn()}
        onOpenPaper={vi.fn()}
      />,
    );

    expect(screen.getByRole('complementary', { name: '论文上下文' })).toBeInTheDocument();
    expect(screen.getByText('Evidence-bound Interfaces')).toBeInTheDocument();
    expect(screen.getByText('证据约束界面')).toBeInTheDocument();
    expect(screen.getByText('UIST · 2026 · Research · Human-AI Interaction')).toBeInTheDocument();
    expect(screen.getByText('第 2 / 7 轮')).toBeInTheDocument();
    expect(screen.getByText('已有笔记')).toBeInTheDocument();
    expect(screen.getByText('A verified summary returned with this paper.')).toBeInTheDocument();
  });

  it('opens the paper id captured by the selected inspector action', async () => {
    const user = userEvent.setup();
    const onOpenPaper = vi.fn();

    render(
      <PaperInspector
        paper={inspectedPaper}
        mode="rail"
        open
        onClose={vi.fn()}
        onOpenPaper={onOpenPaper}
      />,
    );

    await user.click(screen.getByRole('button', { name: '打开 Evidence-bound Interfaces' }));
    expect(onOpenPaper).toHaveBeenCalledWith('paper-a');
  });

  it('exports only the selected paper through the existing inspector actions', async () => {
    const user = userEvent.setup();
    const onExportObsidian = vi.fn();
    const view = render(
      <PaperInspector
        paper={inspectedPaper}
        mode="rail"
        open
        onClose={vi.fn()}
        onOpenPaper={vi.fn()}
        onExportObsidian={onExportObsidian}
      />,
    );

    await user.click(screen.getByRole('button', {
      name: '导出 Evidence-bound Interfaces 到 Obsidian',
    }));
    expect(onExportObsidian).toHaveBeenCalledWith('paper-a');

    view.rerender(
      <PaperInspector
        paper={null}
        mode="rail"
        open
        onClose={vi.fn()}
        onOpenPaper={vi.fn()}
        onExportObsidian={onExportObsidian}
      />,
    );
    expect(screen.queryByRole('button', { name: /导出.*Obsidian/ })).not.toBeInTheDocument();
  });

  it('keeps Obsidian export pending and error states recoverable', async () => {
    const user = userEvent.setup();
    const onExportObsidian = vi.fn();
    const view = render(
      <PaperInspector
        paper={inspectedPaper}
        mode="rail"
        open
        onClose={vi.fn()}
        onOpenPaper={vi.fn()}
        onExportObsidian={onExportObsidian}
        obsidianExportPending
      />,
    );

    expect(screen.getByRole('button', { name: '正在导出到 Obsidian…' })).toBeDisabled();
    view.rerender(
      <PaperInspector
        paper={inspectedPaper}
        mode="rail"
        open
        onClose={vi.fn()}
        onOpenPaper={vi.fn()}
        onExportObsidian={onExportObsidian}
        obsidianExportError="Obsidian 导出失败"
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('Obsidian 导出失败');
    const retry = screen.getByRole('button', {
      name: '导出 Evidence-bound Interfaces 到 Obsidian',
    });
    expect(retry).toBeEnabled();
    await user.click(retry);
    expect(onExportObsidian).toHaveBeenCalledWith('paper-a');
  });

  it('closes a drawer on Escape and scrim interaction', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const view = render(
      <PaperInspector
        paper={inspectedPaper}
        mode="drawer"
        open
        onClose={onClose}
        onOpenPaper={vi.fn()}
      />,
    );

    await user.keyboard('{Escape}');
    expect(onClose).toHaveBeenLastCalledWith('escape');

    view.rerender(
      <PaperInspector
        paper={inspectedPaper}
        mode="drawer"
        open
        onClose={onClose}
        onOpenPaper={vi.fn()}
      />,
    );
    await user.click(screen.getByRole('button', { name: '关闭论文上下文遮罩' }));
    expect(onClose).toHaveBeenLastCalledWith('scrim');
  });

  it('closes an open modal inspector when its responsive mode changes', () => {
    const onClose = vi.fn();
    const view = render(
      <PaperInspector
        paper={inspectedPaper}
        mode="drawer"
        open
        onClose={onClose}
        onOpenPaper={vi.fn()}
      />,
    );

    view.rerender(
      <PaperInspector
        paper={inspectedPaper}
        mode="sheet"
        open
        onClose={onClose}
        onOpenPaper={vi.fn()}
      />,
    );

    expect(onClose).toHaveBeenLastCalledWith('breakpoint');
  });
});
