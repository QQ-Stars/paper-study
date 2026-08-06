import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';

import { isAbortError } from '../../lib/api/errors';
import { paperKeys } from '../../lib/api/keys';
import type { PdfScanFile } from '../../lib/api/types';
import { workspaceApi } from '../../lib/api/workspaceApi';
import type {
  DownloadPdfsTerminal,
  ImportPdfsTerminal,
  LineProgressEvent,
} from '../../lib/streaming/contracts';

type LocalPdfStatus =
  | 'idle'
  | 'scanning'
  | 'ready'
  | 'importing'
  | 'downloading'
  | 'success'
  | 'failure'
  | 'stopped';

function messageFor(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function LocalPdfPanel() {
  const queryClient = useQueryClient();
  const [directory, setDirectory] = useState('');
  const [files, setFiles] = useState<PdfScanFile[]>([]);
  const [selectedPaths, setSelectedPaths] = useState<string[]>([]);
  const [status, setStatus] = useState<LocalPdfStatus>('idle');
  const [progress, setProgress] = useState<string[]>([]);
  const [summary, setSummary] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const ownerRef = useRef<AbortController | null>(null);

  useEffect(() => () => {
    ownerRef.current?.abort();
    ownerRef.current = null;
  }, []);

  const begin = (nextStatus: LocalPdfStatus): AbortController => {
    ownerRef.current?.abort();
    const controller = new AbortController();
    ownerRef.current = controller;
    setStatus(nextStatus);
    setProgress([]);
    setSummary(null);
    setError(null);
    return controller;
  };

  const finishOwner = (controller: AbortController) => {
    if (ownerRef.current === controller) ownerRef.current = null;
  };

  const stop = () => {
    const owner = ownerRef.current;
    if (!owner) return;
    ownerRef.current = null;
    owner.abort();
    setStatus('stopped');
    setSummary('已停止接收；服务端可能仍在运行。');
  };

  const scan = async () => {
    const path = directory.trim();
    if (!path) {
      setError('请输入 PDF 文件夹');
      return;
    }
    const controller = begin('scanning');
    try {
      const result = await workspaceApi.scanPdfs(path, controller.signal);
      if (ownerRef.current !== controller) return;
      setFiles(result.files);
      setSelectedPaths(result.files.map((file) => file.path));
      setStatus('ready');
      setSummary(`TOTAL ${result.count}`);
    } catch (caught) {
      if (ownerRef.current !== controller || isAbortError(caught)) return;
      setStatus('failure');
      setError(messageFor(caught));
    } finally {
      finishOwner(controller);
    }
  };

  const appendProgress = (
    controller: AbortController,
    event: LineProgressEvent | ImportPdfsTerminal | DownloadPdfsTerminal,
  ) => {
    if (
      ownerRef.current === controller
      && event.type === 'progress'
      && event.line.trim()
    ) {
      setProgress((current) => [...current, event.line]);
    }
  };

  const importSelected = async () => {
    const paths = [...selectedPaths];
    if (paths.length === 0) {
      setError('请选择至少一个 PDF');
      return;
    }
    const controller = begin('importing');
    try {
      const result = await workspaceApi.importPdfs(paths, true, {
        signal: controller.signal,
        onEvent: (event) => appendProgress(controller, event),
      });
      if (ownerRef.current !== controller) return;
      setStatus('success');
      setSummary(`PARSED ${paths.length} · ADDED ${result.added} · DUP ${result.dup} · SKIP ${result.failed}`);
    } catch (caught) {
      if (ownerRef.current !== controller || isAbortError(caught)) return;
      setStatus('failure');
      setError(messageFor(caught));
    } finally {
      await queryClient.invalidateQueries({ queryKey: paperKeys.all() });
      finishOwner(controller);
    }
  };

  const downloadMissing = async () => {
    const controller = begin('downloading');
    try {
      const result = await workspaceApi.downloadPdfs({}, {
        signal: controller.signal,
        onEvent: (event) => appendProgress(controller, event),
      });
      if (ownerRef.current !== controller) return;
      setStatus('success');
      setSummary(
        `TOTAL ${result.total} · DOWNLOADED ${result.downloaded} · SKIP ${result.skipped} · FAILED ${result.failed}`,
      );
    } catch (caught) {
      if (ownerRef.current !== controller || isAbortError(caught)) return;
      setStatus('failure');
      setError(messageFor(caught));
    } finally {
      await queryClient.invalidateQueries({ queryKey: paperKeys.all() });
      finishOwner(controller);
    }
  };

  const busy = status === 'scanning' || status === 'importing' || status === 'downloading';
  const selected = new Set(selectedPaths);

  return (
    <section className="local-pdf" aria-label="本地 PDF">
      <header>
        <div>
          <span className="section-kicker">LOCAL INDEX</span>
          <h2>本地 PDF</h2>
        </div>
        <button type="button" onClick={downloadMissing} disabled={busy}>
          补齐馆藏 PDF
        </button>
      </header>

      <div className="local-pdf__controls">
        <label>
          <span>PDF 文件夹</span>
          <input
            value={directory}
            onChange={(event) => setDirectory(event.currentTarget.value)}
            placeholder="C:/Research/Papers"
          />
        </label>
        <button type="button" onClick={scan} disabled={busy}>扫描文件夹</button>
        {busy ? <button type="button" onClick={stop}>停止接收</button> : null}
      </div>

      {files.length > 0 ? (
        <div className="local-pdf__files">
          <div className="local-pdf__select-all">
            <label>
              <input
                type="checkbox"
                checked={files.every((file) => selected.has(file.path))}
                onChange={(event) => {
                  setSelectedPaths(event.currentTarget.checked ? files.map((file) => file.path) : []);
                }}
              />
              选择全部 {files.length} 个文件
            </label>
            <button type="button" onClick={importSelected} disabled={busy || selectedPaths.length === 0}>
              导入选中 PDF
            </button>
          </div>
          <ul>
            {files.map((file) => (
              <li key={file.path}>
                <label>
                  <input
                    type="checkbox"
                    aria-label={`选择 ${file.name}`}
                    checked={selected.has(file.path)}
                    onChange={(event) => {
                      setSelectedPaths((current) => event.currentTarget.checked
                        ? [...new Set([...current, file.path])]
                        : current.filter((path) => path !== file.path));
                    }}
                  />
                  <span>{file.name}</span>
                  <small>{formatBytes(file.size)}</small>
                </label>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="local-pdf__status" aria-live="polite">
        {summary ? <strong>{summary}</strong> : null}
        {error ? <p role="alert">{error}</p> : null}
        {progress.length > 0 ? <pre aria-label="本地 PDF 进度">{progress.join('\n')}</pre> : null}
      </div>
    </section>
  );
}
