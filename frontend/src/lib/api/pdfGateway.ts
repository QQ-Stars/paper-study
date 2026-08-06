import { api, type ApiClient } from './client';
import { decodePdfScanCommand, decodePdfStatus } from './decoders';
import {
  businessFailure,
  signalOptions,
  streamOptions,
  type StreamCommandOptions,
} from './gatewayTransport';
import {
  downloadPdfsContract,
  importPdfsContract,
} from '../streaming/contracts';
import type {
  DownloadPdfsTerminal,
  ImportPdfsTerminal,
  LineProgressEvent,
} from '../streaming/contracts';

export function createPdfGateway(client: ApiClient = api) {
  return {
    scanPdfs(directory: string, signal?: AbortSignal) {
      const dir = String(directory);
      const url = `/api/scan-pdfs?${new URLSearchParams({ dir }).toString()}`;
      return client.json(url, decodePdfScanCommand, signalOptions(signal));
    },

    async importPdfs(
      paths: string[],
      enrich = true,
      options: StreamCommandOptions<LineProgressEvent, ImportPdfsTerminal> = {},
    ) {
      const terminal = await client.ndjson('/api/import-pdfs', importPdfsContract, streamOptions({ paths, enrich }, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    async downloadPdfs(
      input: { ids?: string[]; limit?: number } = {},
      options: StreamCommandOptions<LineProgressEvent, DownloadPdfsTerminal> = {},
    ) {
      const terminal = await client.ndjson('/api/download-pdfs', downloadPdfsContract, streamOptions(input, options));
      if (!terminal.ok) return businessFailure(terminal);
      return terminal;
    },

    getPdfStatus(id: string, signal?: AbortSignal) {
      const paperId = String(id);
      const url = `/api/pdf/status?${new URLSearchParams({ id: paperId }).toString()}`;
      return client.json(url, decodePdfStatus, signalOptions(signal));
    },
  };
}

export const pdfGateway = createPdfGateway();
export type PdfGateway = ReturnType<typeof createPdfGateway>;
