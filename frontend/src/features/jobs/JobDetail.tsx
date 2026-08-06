import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { isAbortError } from '../../lib/api/errors';
import { jobKeys, paperKeys } from '../../lib/api/keys';
import type { Candidate } from '../../lib/api/types';
import { workspaceApi } from '../../lib/api/workspaceApi';
import { jobDetailPollingIntervalFor } from './jobPolling';

interface JobDetailProps {
  jobId: number;
}

function candidateKey(candidate: Candidate, index: number): string {
  return candidate.candidateId === null
    ? `${candidate.source}:${candidate.sourceId}:${index}`
    : `job:${candidate.candidateId}`;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function addedFromFailure(error: unknown): number {
  if (!error || typeof error !== 'object') return 0;
  const body = Reflect.get(error, 'body');
  if (!body || typeof body !== 'object') return 0;
  const added = Reflect.get(body, 'added');
  return typeof added === 'number' && Number.isFinite(added) && added > 0
    ? Math.trunc(added)
    : 0;
}

function progressLine(event: unknown): string | null {
  if (!event || typeof event !== 'object') return null;
  const line = Reflect.get(event, 'line');
  return Reflect.get(event, 'type') === 'progress' && typeof line === 'string'
    ? line
    : null;
}

export function JobDetail({ jobId }: JobDetailProps) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [candidatePanel, setCandidatePanel] = useState<{ jobId: number; open: boolean } | null>(null);
  const [selection, setSelection] = useState<{ jobId: number; keys: string[] } | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [progress, setProgress] = useState<string[]>([]);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const confirmOwnerRef = useRef<AbortController | null>(null);

  const detailQuery = useQuery({
    queryKey: jobKeys.detail(jobId),
    queryFn: ({ signal }) => workspaceApi.getJob(jobId, signal),
    refetchInterval: (query) => {
      const detail = query.state.data;
      const candidatesExpanded = candidatePanel?.jobId === jobId
        ? candidatePanel.open
        : detail?.job.status === 'review';
      return jobDetailPollingIntervalFor(detail, candidatesExpanded);
    },
  });

  useEffect(() => () => {
    confirmOwnerRef.current?.abort();
    confirmOwnerRef.current = null;
  }, []);

  const candidateEntries = useMemo(
    () => (detailQuery.data?.candidates ?? []).map((candidate, index) => ({
      candidate,
      key: candidateKey(candidate, index),
    })),
    [detailQuery.data?.candidates],
  );

  const eligibleKeys = candidateEntries
    .filter(({ candidate }) => !candidate.inLibrary)
    .map(({ key }) => key);
  const selectedKeys = selection?.jobId === jobId
    ? selection.keys.filter((key) => eligibleKeys.includes(key))
    : eligibleKeys;

  const confirmSelected = async () => {
    const selected = new Set(selectedKeys);
    const fixedCandidates = candidateEntries
      .filter(({ key, candidate }) => selected.has(key) && !candidate.inLibrary)
      .map(({ candidate }) => candidate);
    if (fixedCandidates.length === 0) {
      setError('请先选择要确认的候选');
      return;
    }
    confirmOwnerRef.current?.abort();
    const controller = new AbortController();
    confirmOwnerRef.current = controller;
    setConfirming(true);
    setProgress([]);
    setResult(null);
    setError(null);
    let added = 0;
    try {
      const terminal = await workspaceApi.confirmJob(
        jobId,
        { candidates: fixedCandidates, downloadPdf: true },
        {
          signal: controller.signal,
          onEvent: (event) => {
            const line = progressLine(event);
            if (confirmOwnerRef.current === controller && line) {
              setProgress((current) => [...current, line]);
            }
          },
        },
      );
      if (confirmOwnerRef.current !== controller) return;
      added = terminal.added;
      setResult(`服务器确认新增 ${added} 篇。`);
    } catch (caught) {
      added = addedFromFailure(caught);
      if (confirmOwnerRef.current !== controller || isAbortError(caught)) return;
      setError(errorMessage(caught));
    } finally {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: jobKeys.list() }),
        queryClient.invalidateQueries({ queryKey: jobKeys.detail(jobId) }),
      ]);
      if (added > 0) {
        await queryClient.invalidateQueries({ queryKey: paperKeys.all() });
      }
      if (confirmOwnerRef.current === controller) {
        confirmOwnerRef.current = null;
        setConfirming(false);
      }
    }
  };

  const stopConfirm = () => {
    const controller = confirmOwnerRef.current;
    if (!controller) return;
    confirmOwnerRef.current = null;
    controller.abort();
    setConfirming(false);
    setResult('已停止接收；服务端可能仍在运行。');
    void Promise.all([
      queryClient.invalidateQueries({ queryKey: jobKeys.list() }),
      queryClient.invalidateQueries({ queryKey: jobKeys.detail(jobId) }),
    ]);
  };

  const deleteJob = async () => {
    if (!globalThis.confirm(`删除任务 ${jobId} 及其待确认候选？`)) return;
    setError(null);
    try {
      await workspaceApi.deleteJob(jobId);
      await queryClient.invalidateQueries({ queryKey: jobKeys.list() });
      navigate('/jobs');
    } catch (caught) {
      setError(errorMessage(caught));
    }
  };

  if (detailQuery.isPending) {
    return <section className="job-detail" aria-label={`任务 ${jobId} 详情`}><p>读取任务详情…</p></section>;
  }
  if (detailQuery.isError || !detailQuery.data) {
    return (
      <section className="job-detail" aria-label={`任务 ${jobId} 详情`}>
        <p role="alert">{errorMessage(detailQuery.error ?? '任务不存在')}</p>
        <div className="job-detail__actions">
          <button type="button" onClick={() => void detailQuery.refetch()}>
            重试读取任务
          </button>
        </div>
      </section>
    );
  }

  const detail = detailQuery.data;
  const showCandidates = candidatePanel?.jobId === jobId
    ? candidatePanel.open
    : detail.job.status === 'review';
  const eligibleCount = candidateEntries.filter(({ candidate }) => !candidate.inLibrary).length;

  return (
    <section className="job-detail" aria-label={`任务 ${jobId} 详情`}>
      <header className="job-detail__header">
        <div>
          <span className="jobs-kicker">JOB / {jobId}</span>
          <h2>{detail.job.query || `任务 ${jobId}`}</h2>
          <p>{detail.job.sources.join(' · ') || '未记录来源'}</p>
        </div>
        <span className={`job-status job-status--${detail.job.status}`}>{detail.job.status}</span>
      </header>

      <dl className="job-detail__metrics">
        <div><dt>FOUND</dt><dd>{detail.job.found}</dd></div>
        <div><dt>ADDED</dt><dd>{detail.job.added}</dd></div>
        <div><dt>SKIPPED</dt><dd>{detail.job.skipped}</dd></div>
        <div><dt>CANDIDATES</dt><dd>{detail.candidates.length}</dd></div>
      </dl>

      <div className="job-detail__actions">
        <button
          type="button"
          onClick={() => setCandidatePanel({ jobId, open: !showCandidates })}
        >
          {showCandidates ? '收起候选详情' : '展开候选详情'}
        </button>
        <button type="button" onClick={deleteJob}>删除任务</button>
      </div>

      {showCandidates ? (
        <div className="job-candidates">
          {candidateEntries.length === 0 ? (
            <p className="job-candidates__empty">当前任务没有待确认候选。</p>
          ) : (
            <>
              <div className="job-candidates__toolbar">
                <label>
                  <input
                    type="checkbox"
                    checked={eligibleCount > 0 && selectedKeys.length === eligibleCount}
                    onChange={(event) => setSelection({
                      jobId,
                      keys: event.currentTarget.checked ? eligibleKeys : [],
                    })}
                  />
                  选择全部待确认候选
                </label>
                <span>{selectedKeys.length} / {eligibleCount}</span>
              </div>
              <ul>
                {candidateEntries.map(({ candidate, key }) => (
                  <li key={key}>
                    <label>
                      <input
                        type="checkbox"
                        aria-label={`选择 ${candidate.title}`}
                        checked={!candidate.inLibrary && selectedKeys.includes(key)}
                        disabled={candidate.inLibrary || confirming}
                        onChange={(event) => setSelection({
                          jobId,
                          keys: event.currentTarget.checked
                            ? [...new Set([...selectedKeys, key])]
                            : selectedKeys.filter((item) => item !== key),
                        })}
                      />
                      <span>
                        <strong>{candidate.title}</strong>
                        <small>{[candidate.venue, candidate.year, candidate.ccf ? `CCF ${candidate.ccf}` : null]
                          .filter(Boolean).join(' · ')}</small>
                      </span>
                      {candidate.inLibrary ? <b>已在库</b> : null}
                    </label>
                  </li>
                ))}
              </ul>
              <div className="job-candidates__actions">
                <button type="button" onClick={confirmSelected} disabled={confirming || selectedKeys.length === 0}>
                  确认选中候选
                </button>
                {confirming ? <button type="button" onClick={stopConfirm}>停止接收</button> : null}
              </div>
            </>
          )}
        </div>
      ) : null}

      {result ? <p className="job-detail__result" role="status">{result}</p> : null}
      {error ? <p className="job-detail__error" role="alert">{error}</p> : null}
      {progress.length > 0 ? <pre aria-label="任务确认进度">{progress.join('\n')}</pre> : null}
      {detail.job.log ? <pre aria-label="任务日志">{detail.job.log}</pre> : null}
    </section>
  );
}
