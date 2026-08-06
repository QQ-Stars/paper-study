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
}

function parsingClient(): MarkdownWorkerClient {
  return {
    render: async (source) => parseMarkdown(source),
    cancel: () => undefined,
    dispose: () => undefined,
  };
}

describe('MarkdownContent', () => {
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

  it('shows the source as inert text while rendering is pending', () => {
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
        source="<strong>pending</strong>"
        generation={2}
        workerClientFactory={workerClientFactory}
      />,
    );

    expect(container.textContent).toBe('<strong>pending</strong>');
    expect(container.querySelector('strong')).toBeNull();
    expect(workers).toHaveLength(1);
    unmount();
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
