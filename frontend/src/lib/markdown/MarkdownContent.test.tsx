import { StrictMode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { parseMarkdown, type SafeDocument } from './ast';
import { MarkdownContent } from './MarkdownContent';
import {
  createMarkdownWorkerClient,
  type MarkdownWorkerClient,
  type MarkdownWorkerLike,
  type MarkdownWorkerRequest,
} from './workerClient';

class ControlledWorker implements MarkdownWorkerLike {
  static live = 0;

  onmessage: ((event: MessageEvent<unknown>) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessageerror: ((event: MessageEvent<unknown>) => void) | null = null;
  readonly sent: unknown[] = [];
  terminated = false;

  constructor() {
    ControlledWorker.live += 1;
  }

  postMessage(message: unknown): void {
    this.sent.push(message);
  }

  terminate(): void {
    if (this.terminated) return;
    this.terminated = true;
    ControlledWorker.live -= 1;
  }

  emit(document: SafeDocument): void {
    const request = this.sent[0] as MarkdownWorkerRequest;
    this.onmessage?.({
      data: {
        id: request.id,
        generation: request.generation,
        document,
      },
    } as MessageEvent<unknown>);
  }

  emitError(): void {
    this.onerror?.(new Event('error'));
  }
}

function parsingClient(): MarkdownWorkerClient {
  return {
    render: async (source) => ({
      status: 'parsed',
      document: parseMarkdown(source),
    }),
    cancel: () => undefined,
    dispose: () => undefined,
  };
}

describe('MarkdownContent', () => {
  it('offsets embedded document headings without changing their visual depth metadata', async () => {
    const { container } = render(
      <MarkdownContent
        source={'# 讲解标题\n\n## 研究问题'}
        generation={1}
        headingLevelOffset={2}
        workerClientFactory={parsingClient}
      />,
    );

    const title = await screen.findByRole('heading', { level: 3, name: '讲解标题' });
    expect(title).toHaveAttribute('data-markdown-depth', '1');
    expect(screen.getByRole('heading', { level: 4, name: '研究问题' })).toHaveAttribute(
      'data-markdown-depth',
      '2',
    );
    expect(container.querySelector('h1')).toBeNull();
  });

  it('renders explainer Markdown as semantic document structure', async () => {
    const source = [
      '# 论文讲解',
      '',
      '> 本文提出一种新的研究方法。',
      '',
      '## 研究问题',
      '',
      '**多模态大模型**需要识别*知识边界*与 `unknown` 状态。',
      '',
      '- 识别已知知识',
      '- 拒绝未知问题',
      '- [x] 完成安全解析',
      '',
      '1. 建立基线',
      '2. 评估结果',
      '',
      '---',
      '',
      '| 模块 | 结果 |',
      '| --- | ---: |',
      '| 校准 | 7.5% |',
      '',
      '~~旧结论~~与新结论。',
      '',
      '第一行  ',
      '第二行',
      '',
      '```python',
      'score = model(sample)',
      '```',
      '',
      '[阅读论文](https://example.com/paper) 并验证 $x^2$',
    ].join('\n');
    const { container } = render(
      <MarkdownContent
        source={source}
        generation={1}
        workerClientFactory={parsingClient}
      />,
    );

    expect(await screen.findByRole('heading', { level: 1, name: '论文讲解' })).toBeVisible();
    expect(screen.getByRole('heading', { level: 2, name: '研究问题' })).toBeVisible();
    expect(container.querySelector('blockquote')).toHaveTextContent('本文提出一种新的研究方法。');
    expect(container.querySelector('strong')).toHaveTextContent('多模态大模型');
    expect(container.querySelector('em')).toHaveTextContent('知识边界');
    expect(container.querySelector('p code')).toHaveTextContent('unknown');
    expect(container.querySelector('ul')).toHaveTextContent('识别已知知识');
    expect(container.querySelector('input[type="checkbox"]')).toBeChecked();
    expect(container.querySelector('ol')).toHaveTextContent('建立基线');
    expect(container.querySelector('hr')).not.toBeNull();
    expect(container.querySelector('table')).toHaveTextContent('校准7.5%');
    expect(container.querySelector('th[data-align="right"], td[data-align="right"]')).not.toBeNull();
    expect(container.querySelector('del')).toHaveTextContent('旧结论');
    expect(container.querySelector('br')).not.toBeNull();
    expect(container.querySelector('pre code[data-language="python"]')).toHaveTextContent('score = model(sample)');
    expect(screen.getByRole('link', { name: '阅读论文' })).toHaveAttribute('href', 'https://example.com/paper');
    expect(container.querySelector('.katex')).not.toBeNull();
    expect(container.textContent).not.toContain('## 研究问题');
  });

  it('renders safe AST nodes without turning hostile Markdown into active DOM', async () => {
    const source = [
      '<img src=x onerror=alert(1)>',
      '',
      '![tracker alt](data:image/svg+xml,hostile)',
      '',
      '[script](javascript:alert(1)) [file](file:///secret) [relative](./paper)',
      '',
      '[safe](https://example.com/paper) and $x^2$',
    ].join('\n');
    const { container } = render(
      <MarkdownContent
        source={source}
        generation={1}
        workerClientFactory={parsingClient}
      />,
    );

    const safeLink = await screen.findByRole('link', { name: 'safe' });
    expect(safeLink).toHaveAttribute('href', 'https://example.com/paper');
    expect(container.querySelectorAll('a')).toHaveLength(1);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('script')).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>');
    expect(container.textContent).toContain('tracker alt');
    expect(container.textContent).toContain('script file relative');
    expect(container.querySelector('.katex')).not.toBeNull();
  });

  it('shows an accessible formatting state without flashing raw Markdown while pending', () => {
    ControlledWorker.live = 0;
    const workers: ControlledWorker[] = [];
    const workerClientFactory = () => createMarkdownWorkerClient({
      workerFactory: () => {
        const worker = new ControlledWorker();
        workers.push(worker);
        return worker;
      },
    });

    const { container, unmount } = render(
      <MarkdownContent
        source={'## Pending\n\n**body**\n\n> quote'}
        generation={2}
        workerClientFactory={workerClientFactory}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('正在排版内容…');
    expect(container.firstElementChild).toHaveAttribute('data-markdown-state', 'pending');
    expect(container.textContent).not.toContain('## Pending');
    expect(container.textContent).not.toContain('**body**');
    expect(container.textContent).not.toContain('> quote');
    expect(container.querySelector('strong')).toBeNull();
    expect(workers).toHaveLength(1);
    unmount();
    expect(ControlledWorker.live).toBe(0);
  });

  it('marks a Worker failure as pre-wrapped inert plain text', async () => {
    ControlledWorker.live = 0;
    const source = '## Fallback title\n\n**fallback body**';
    const workers: ControlledWorker[] = [];
    const workerClientFactory = () => createMarkdownWorkerClient({
      workerFactory: () => {
        const worker = new ControlledWorker();
        workers.push(worker);
        return worker;
      },
    });
    const { container } = render(
      <MarkdownContent
        source={source}
        generation={2}
        workerClientFactory={workerClientFactory}
      />,
    );

    workers[0]?.emitError();

    await waitFor(() => {
      expect(container.firstElementChild).toHaveAttribute('data-markdown-state', 'plain-text');
    });
    expect(container.textContent).toBe(source);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(container.querySelector('h2')).toBeNull();
    expect(container.querySelector('strong')).toBeNull();
    expect(ControlledWorker.live).toBe(0);
  });

  it('disposes the previous owner when source and generation change', async () => {
    ControlledWorker.live = 0;
    const workers: ControlledWorker[] = [];
    const workerClientFactory = () => createMarkdownWorkerClient({
      workerFactory: () => {
        const worker = new ControlledWorker();
        workers.push(worker);
        return worker;
      },
    });
    const view = render(
      <MarkdownContent
        source="old"
        generation={3}
        workerClientFactory={workerClientFactory}
      />,
    );

    view.rerender(
      <MarkdownContent
        source="new"
        generation={4}
        workerClientFactory={workerClientFactory}
      />,
    );

    expect(workers[0]?.terminated).toBe(true);
    expect(workers[1]?.terminated).toBe(false);
    workers[1]?.emit(parseMarkdown('**current**'));
    await waitFor(() => expect(view.container).toHaveTextContent('current'));
    expect(ControlledWorker.live).toBe(0);
  });

  it('leaves exactly one live Worker after the StrictMode probe and none after unmount', () => {
    ControlledWorker.live = 0;
    const workers: ControlledWorker[] = [];
    const workerClientFactory = () => createMarkdownWorkerClient({
      workerFactory: () => {
        const worker = new ControlledWorker();
        workers.push(worker);
        return worker;
      },
    });
    const view = render(
      <StrictMode>
        <MarkdownContent
          source="pending"
          generation={5}
          workerClientFactory={workerClientFactory}
        />
      </StrictMode>,
    );

    expect(workers).toHaveLength(2);
    expect(workers[0]?.terminated).toBe(true);
    expect(workers[1]?.terminated).toBe(false);
    expect(ControlledWorker.live).toBe(1);

    view.unmount();
    expect(ControlledWorker.live).toBe(0);
  });
});
