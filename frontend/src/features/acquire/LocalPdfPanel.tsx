import { useQueryClient } from '@tanstack/react-query';
import {
  useEffect,
  useReducer,
  useRef,
  useState,
} from 'react';

import { isAbortError } from '../../lib/api/errors';
import { paperKeys } from '../../lib/api/keys';
import type { PdfScanFile } from '../../lib/api/types';
import { pdfGateway } from '../../lib/api/pdfGateway';
import type {
  DownloadPdfsTerminal,
  ImportPdfsTerminal,
  LineProgressEvent,
} from '../../lib/streaming/contracts';
import {
  createLocalPdfSessionState,
  localPdfSessionReducer,
  type LocalPdfOperation,
} from './localPdfReducer';

interface LocalPdfRunOwner {
  readonly runId: number;
  readonly controller: AbortController;
}

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
  const [session, dispatch] = useReducer(
    localPdfSessionReducer,
    undefined,
    createLocalPdfSessionState,
  );
  const runSequenceRef = useRef(0);
  const ownerRef = useRef<LocalPdfRunOwner | null>(null);

  useEffect(() => () => {
    ownerRef.current?.controller.abort();
    ownerRef.current = null;
  }, []);

  const begin = (operation: LocalPdfOperation): LocalPdfRunOwner => {
    ownerRef.current?.controller.abort();
    const runId = ++runSequenceRef.current;
    const controller = new AbortController();
    const owner = { runId, controller };
    ownerRef.current = owner;
    dispatch({ type: 'start', runId, operation });
    return owner;
  };

  const finishOwner = (owner: LocalPdfRunOwner) => {
    if (ownerRef.current === owner) ownerRef.current = null;
  };

  const stop = () => {
    const owner = ownerRef.current;
    if (!owner) return;
    ownerRef.current = null;
    owner.controller.abort();
    dispatch({
      type: 'stop',
      runId: owner.runId,
      terminal: '已停止接收；服务端可能仍在运行。',
    });
  };

  const scan = async () => {
    const path = directory.trim();
    if (!path) {
      dispatch({ type: 'validation-failure', error: '请输入 PDF 文件夹' });
      return;
    }
    const owner = begin('scan');
    try {
      const result = await pdfGateway.scanPdfs(path, owner.controller.signal);
      if (ownerRef.current !== owner) return;
      setFiles(result.files);
      setSelectedPaths(result.files.map((file) => file.path));
      dispatch({ type: 'ready', runId: owner.runId, terminal: `TOTAL ${result.count}` });
    } catch (caught) {
      if (ownerRef.current !== owner || isAbortError(caught)) return;
      dispatch({ type: 'failure', runId: owner.runId, error: messageFor(caught) });
    } finally {
      finishOwner(owner);
    }
  };

  const appendProgress = (
    owner: LocalPdfRunOwner,
    event: LineProgressEvent | ImportPdfsTerminal | DownloadPdfsTerminal,
  ) => {
    if (
      ownerRef.current === owner
      && event.type === 'progress'
      && event.line.trim()
    ) {
      dispatch({ type: 'progress', runId: owner.runId, line: event.line });
    }
  };

  const importSelected = async () => {
    const paths = [...selectedPaths];
    if (paths.length === 0) {
      dispatch({ type: 'validation-failure', error: '请选择至少一个 PDF' });
      return;
    }
    const owner = begin('import');
    try {
      const result = await pdfGateway.importPdfs(paths, true, {
        signal: owner.controller.signal,
        onEvent: (event) => appendProgress(owner, event),
      });
      if (ownerRef.current !== owner) return;
      dispatch({
        type: 'success',
        runId: owner.runId,
        terminal: `PARSED ${paths.length} · ADDED ${result.added} · DUP ${result.dup} · SKIP ${result.failed}`,
      });
    } catch (caught) {
      if (ownerRef.current !== owner || isAbortError(caught)) return;
      dispatch({ type: 'failure', runId: owner.runId, error: messageFor(caught) });
    } finally {
      await queryClient.invalidateQueries({ queryKey: paperKeys.all() });
      finishOwner(owner);
    }
  };

  const downloadMissing = async () => {
    const owner = begin('download');
    try {
      const result = await pdfGateway.downloadPdfs({}, {
        signal: owner.controller.signal,
        onEvent: (event) => appendProgress(owner, event),
      });
      if (ownerRef.current !== owner) return;
      dispatch({
        type: 'success',
        runId: owner.runId,
        terminal: `TOTAL ${result.total} · DOWNLOADED ${result.downloaded} · SKIP ${result.skipped} · FAILED ${result.failed}`,
      });
    } catch (caught) {
      if (ownerRef.current !== owner || isAbortError(caught)) return;
      dispatch({ type: 'failure', runId: owner.runId, error: messageFor(caught) });
    } finally {
      await queryClient.invalidateQueries({ queryKey: paperKeys.all() });
      finishOwner(owner);
    }
  };

  const busy = session.phase === 'running';
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
        {session.terminal ? <strong>{session.terminal}</strong> : null}
        {session.error ? <p role="alert">{session.error}</p> : null}
        {session.progress.length > 0 ? (
          <pre aria-label="本地 PDF 进度">{session.progress.join('\n')}</pre>
        ) : null}
      </div>
    </section>
  );
}
