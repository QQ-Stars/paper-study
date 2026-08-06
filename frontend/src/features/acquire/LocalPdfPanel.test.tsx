import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { StreamCommandOptions } from '../../lib/api/gatewayTransport';
import type {
  ImportPdfsTerminal,
  LineProgressEvent,
} from '../../lib/streaming/contracts';
import { LocalPdfPanel } from './LocalPdfPanel';

const apiMocks = vi.hoisted(() => ({
  downloadPdfs: vi.fn(),
  importPdfs: vi.fn(),
  scanPdfs: vi.fn(),
}));

vi.mock('../../lib/api/pdfGateway', () => ({
  pdfGateway: apiMocks,
}));

function renderPanel() {
  const queryClient = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  });

  return render(
    <QueryClientProvider client={queryClient}>
      <LocalPdfPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  for (const mock of Object.values(apiMocks)) mock.mockReset();
  apiMocks.scanPdfs.mockResolvedValue({
    count: 6,
    dir: 'C:/papers',
    files: Array.from({ length: 6 }, (_, index) => ({
      name: `paper-${index + 1}.pdf`,
      path: `C:/papers/paper-${index + 1}.pdf`,
      size: 1024,
    })),
  });
  apiMocks.downloadPdfs.mockResolvedValue({
    type: 'result',
    ok: true,
    downloaded: 0,
    skipped: 0,
    failed: 0,
    total: 0,
  });
});

describe('Local PDF panel', () => {
  it('reports import outcomes from the stream without treating selected paths or failures as parsed/skipped files', async () => {
    const user = userEvent.setup();
    apiMocks.importPdfs.mockImplementation(async (
      _paths: string[],
      _enrich: boolean,
      options: StreamCommandOptions<LineProgressEvent, ImportPdfsTerminal>,
    ) => {
      for (const line of [
        'TOTAL::5',
        'PARSED::1::5::Paper Alpha',
        'SKIP::2::5::unreadable-a.pdf',
        'PREPERR::metadata service unavailable',
        'SKIP::4::5::unreadable-b.pdf',
        'PARSED::5::5::Paper Beta',
        'ADDED::Paper Alpha',
        'CLSERR::classifier timeout',
      ]) {
        options.onEvent?.({ type: 'progress', line });
      }
      return {
        type: 'result',
        ok: true,
        added: 1,
        dup: 0,
        failed: 1,
        total: 5,
      };
    });
    renderPanel();

    const panel = screen.getByRole('region', { name: '本地 PDF' });
    await user.type(within(panel).getByRole('textbox', { name: 'PDF 文件夹' }), 'C:/papers');
    await user.click(within(panel).getByRole('button', { name: '扫描文件夹' }));
    await user.click(await within(panel).findByRole('button', { name: '导入选中 PDF' }));

    expect(await within(panel).findByLabelText('本地 PDF 导入汇总')).toHaveTextContent(
      'TOTAL 5 · PARSED 2 · ADDED 1 · DUP 0 · SKIP 2 · PREPERR 1 · CLSERR 1',
    );
    expect(within(panel).getByLabelText('本地 PDF 进度')).toHaveTextContent(
      'PREPERR::metadata service unavailable',
    );
    expect(within(panel).getByLabelText('本地 PDF 进度')).toHaveTextContent(
      'CLSERR::classifier timeout',
    );
  });
});
