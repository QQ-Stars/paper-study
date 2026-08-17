/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useReducer, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';

import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { isAbortError } from '../../lib/api/errors';
import { jobKeys, paperKeys } from '../../lib/api/keys';
import type { SearchRequest } from '../../lib/api/types';
import { acquisitionGateway } from '../../lib/api/acquisitionGateway';
import { jobsGateway } from '../../lib/api/jobsGateway';
import { createSafeStorage, createSearchHistory } from '../../lib/storage/safeStorage';
import type { WorkspaceRouteHandle } from '../../lib/workspace';
import {
  ACADEMIC_SOURCES,
  SOURCE_LABELS,
  acquireReducer,
  candidateKey,
  createAcquireState,
  normalizeSearchDraft,
  type AcquireOperation,
} from './acquireReducer';
import { CandidateList } from './CandidateList';
import { LocalPdfPanel } from './LocalPdfPanel';
import './acquire.css';

export const handle = {
  title: '采集',
  layout: 'progress',
} satisfies WorkspaceRouteHandle;

interface RunOwner {
  runId: number;
  operation: AcquireOperation;
  controller: AbortController;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function progressLine(event: unknown): string | null {
  if (!event || typeof event !== 'object') return null;
  if (Reflect.get(event, 'type') !== 'progress') return null;
  const line = Reflect.get(event, 'line');
  return typeof line === 'string' ? line : null;
}

export function Component() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [state, dispatch] = useReducer(acquireReducer, undefined, createAcquireState);
  const [query, setQuery] = useState('');
  const [sources, setSources] = useState<string[]>(['semanticscholar', 'arxiv']);
  const [years, setYears] = useState('2024-2026');
  const [maxCandidates, setMaxCandidates] = useState(10);
  const [minRelevance, setMinRelevance] = useState(0);
  const [expand, setExpand] = useState(false);
  const [onlyA, setOnlyA] = useState(false);
  const [queries, setQueries] = useState<string[]>([]);
  const [validationErrors, setValidationErrors] = useState<string[]>([]);
  const [expandStatus, setExpandStatus] = useState<string | null>(null);
  const [historyStore] = useState(() => createSearchHistory(createSafeStorage()));
  const [history, setHistory] = useState(() => historyStore.list());
  const ownerRef = useRef<RunOwner | null>(null);
  const expandOwnerRef = useRef<AbortController | null>(null);
  const jobOwnerRef = useRef<AbortController | null>(null);
  const runSequence = useRef(0);
  const [lastRequest, setLastRequest] = useState<SearchRequest | null>(null);
  const [submittingJob, setSubmittingJob] = useState(false);

  // 后台任务列表随页面挂载轮询：用户离开再返回采集页时，可直接看到
  // 服务端任务的最新状态（pending/running 时 2.5s 轮询，否则停止）。
  const backgroundJobsQuery = useQuery({
    queryKey: jobKeys.list(),
    queryFn: ({ signal }) => jobsGateway.listJobs(signal),
    refetchInterval: (query) => (
      query.state.data?.some((job) => job.status === 'pending' || job.status === 'running')
        ? 2500
        : false
    ),
  });

  useEffect(() => () => {
    ownerRef.current?.controller.abort();
    ownerRef.current = null;
    expandOwnerRef.current?.abort();
    expandOwnerRef.current = null;
    jobOwnerRef.current?.abort();
    jobOwnerRef.current = null;
  }, []);

  const beginRun = (operation: AcquireOperation): RunOwner => {
    ownerRef.current?.controller.abort();
    const owner = {
      runId: ++runSequence.current,
      operation,
      controller: new AbortController(),
    };
    ownerRef.current = owner;
    dispatch({ type: 'start', operation, runId: owner.runId });
    return owner;
  };

  const finishRun = (owner: RunOwner) => {
    if (ownerRef.current === owner) ownerRef.current = null;
  };

  const onProgress = (owner: RunOwner, event: unknown) => {
    const line = progressLine(event);
    if (line !== null) dispatch({ type: 'progress', runId: owner.runId, line });
  };

  const executeSearch = async (request: SearchRequest) => {
    const fixedRequest: SearchRequest = {
      ...request,
      sources: [...request.sources],
      queries: request.queries ? [...request.queries] : undefined,
    };
    setLastRequest(fixedRequest);
    setHistory(historyStore.add(fixedRequest.query));
    setValidationErrors([]);
    const owner = beginRun('search');
    try {
      const result = await acquisitionGateway.search(fixedRequest, {
        signal: owner.controller.signal,
        onEvent: (event) => onProgress(owner, event),
      });
      dispatch({
        type: 'search-complete',
        runId: owner.runId,
        candidates: result.candidates,
      });
    } catch (error) {
      if (isAbortError(error)) {
        dispatch({ type: 'stop', runId: owner.runId });
      } else {
        dispatch({ type: 'failure', runId: owner.runId, error: errorMessage(error) });
      }
    } finally {
      finishRun(owner);
    }
  };

  const submitSearch = () => {
    const normalized = normalizeSearchDraft({
      query,
      sources,
      years,
      max: maxCandidates,
      minRelevance,
      expand,
      onlyA,
      queries,
    });
    if (!normalized.ok) {
      setValidationErrors(normalized.errors);
      return;
    }
    void executeSearch(normalized.request);
  };

  // 后台检索：提交为服务端任务（POST /api/jobs）后立即跳转任务详情。
  // 任务由后端 asyncio 后台任务驱动 agent 子进程执行，与前端连接无关：
  // 离开页面/关闭标签页不会中断，随时可从任务页或本页「后台任务」面板恢复查看。
  const submitBackgroundSearch = async () => {
    const normalized = normalizeSearchDraft({
      query,
      sources,
      years,
      max: maxCandidates,
      minRelevance,
      expand,
      onlyA,
      queries,
    });
    if (!normalized.ok) {
      setValidationErrors(normalized.errors);
      return;
    }
    setValidationErrors([]);
    setHistory(historyStore.add(normalized.request.query));
    setLastRequest(normalized.request);
    jobOwnerRef.current?.abort();
    const controller = new AbortController();
    jobOwnerRef.current = controller;
    setSubmittingJob(true);
    try {
      const id = await jobsGateway.createJob({
        query: normalized.request.query,
        sources: normalized.request.sources,
        years: normalized.request.years,
        max: normalized.request.max,
        minRelevance: normalized.request.minRelevance,
        onlyA: normalized.request.onlyA,
        queries,
      }, controller.signal);
      if (jobOwnerRef.current !== controller) return;
      await queryClient.invalidateQueries({ queryKey: jobKeys.list() });
      navigate(`/jobs/${id}`);
    } catch (error) {
      if (jobOwnerRef.current !== controller || isAbortError(error)) return;
      setValidationErrors(['后台检索提交失败：' + errorMessage(error)]);
    } finally {
      if (jobOwnerRef.current === controller) {
        jobOwnerRef.current = null;
        setSubmittingJob(false);
      }
    }
  };

  const stopCurrentRun = () => {
    const owner = ownerRef.current;
    if (!owner) return;
    ownerRef.current = null;
    owner.controller.abort();
    dispatch({
      type: 'stop',
      runId: owner.runId,
      reason: '已停止接收；服务端可能仍在运行。',
    });
  };

  const verifyCandidates = async () => {
    if (state.candidates.length === 0) return;
    const fixedCandidates = [...state.candidates];
    const owner = beginRun('verify');
    try {
      const result = await acquisitionGateway.verifyVenue(
        fixedCandidates,
        ['dblp', 'semanticscholar'],
        {
          signal: owner.controller.signal,
          onEvent: (event) => onProgress(owner, event),
        },
      );
      dispatch({
        type: 'verify-complete',
        runId: owner.runId,
        verifications: result.verifications,
      });
    } catch (error) {
      if (isAbortError(error)) dispatch({ type: 'stop', runId: owner.runId });
      else dispatch({ type: 'failure', runId: owner.runId, error: errorMessage(error) });
    } finally {
      finishRun(owner);
    }
  };

  const ingestSelected = async () => {
    const selected = new Set(state.selectedKeys);
    const fixedCandidates = state.candidates.filter((candidate) => selected.has(candidateKey(candidate)));
    if (fixedCandidates.length === 0) {
      setValidationErrors(['请先选择要入库的候选']);
      return;
    }
    setValidationErrors([]);
    const owner = beginRun('ingest');
    try {
      const result = await acquisitionGateway.ingestSelected(
        { candidates: fixedCandidates, downloadPdf: true },
        {
          signal: owner.controller.signal,
          onEvent: (event) => onProgress(owner, event),
        },
      );
      dispatch({ type: 'ingest-complete', runId: owner.runId, added: result.added });
    } catch (error) {
      if (isAbortError(error)) dispatch({ type: 'stop', runId: owner.runId });
      else dispatch({ type: 'failure', runId: owner.runId, error: errorMessage(error) });
    } finally {
      await queryClient.invalidateQueries({ queryKey: paperKeys.all() });
      finishRun(owner);
    }
  };

  const expandQueries = async () => {
    const fixedQuery = query.trim();
    if (!fixedQuery) {
      setValidationErrors(['请输入研究方向']);
      return;
    }
    expandOwnerRef.current?.abort();
    const controller = new AbortController();
    expandOwnerRef.current = controller;
    setExpandStatus('正在生成检索词…');
    try {
      const result = await acquisitionGateway.expand(fixedQuery, 6, controller.signal);
      if (expandOwnerRef.current !== controller) return;
      const expanded = result.queries
        .map((item) => item.trim())
        .filter(Boolean);
      setQueries(expanded.length > 0 ? expanded : [fixedQuery]);
      setExpandStatus(expanded.length > 0 ? `已生成 ${expanded.length} 个检索词。` : '扩展为空，已回退原研究方向。');
    } catch (error) {
      if (expandOwnerRef.current !== controller || isAbortError(error)) return;
      setQueries([fixedQuery]);
      setExpandStatus('扩展失败，已回退原研究方向。');
    } finally {
      if (expandOwnerRef.current === controller) expandOwnerRef.current = null;
    }
  };

  const replaceQuery = (nextQuery: string) => {
    expandOwnerRef.current?.abort();
    expandOwnerRef.current = null;
    setQuery(nextQuery);
    setQueries([]);
    setExpandStatus(null);
    setValidationErrors([]);
  };

  const selectHistory = (item: string) => replaceQuery(item);

  const busy = state.status === 'running';
  const statusMessage = state.status === 'stopped'
    ? state.error || '已停止接收；服务端可能仍在运行。'
    : state.status === 'failure'
      ? state.error
      : state.status === 'success'
        ? `服务器确认新增 ${state.added ?? 0} 篇。`
        : null;

  return (
    <div className="acquire-page">
      {/* sticky 表单卡的包含块仅限双列区：下方的全宽卡片（后台任务/本地 PDF）
          在 .acquire-workspace 之外排布，滚动时不会被钉住的表单卡遮挡。 */}
      <div className="acquire-workspace">
      <section className="acquire-command" aria-label="研究采集命令">
        <header className="acquire-command__header">
          <div>
            <span className="section-kicker">RESEARCH INTAKE</span>
            <h2>构建候选流</h2>
            <p>先检索与核验，确认后才写入论文库。</p>
          </div>
          <div className="acquire-command__status" aria-live="polite">
            <span>{state.status.toUpperCase()}</span>
            <strong>{state.candidates.length}</strong>
            <small>CANDIDATES</small>
          </div>
        </header>

        <div className="acquire-form">
          <label className="acquire-form__query">
            <span>研究方向</span>
            <input
              value={query}
              onChange={(event) => replaceQuery(event.currentTarget.value)}
              placeholder="例如：lifecycle-safe document readers"
            />
          </label>
          <label>
            <span>年份</span>
            <input value={years} onChange={(event) => setYears(event.currentTarget.value)} />
          </label>
          <label>
            <span>最多候选</span>
            <input
              type="number"
              min="1"
              max="60"
              value={maxCandidates}
              onChange={(event) => setMaxCandidates(Number(event.currentTarget.value))}
            />
          </label>
          <label>
            <span>最低相关度</span>
            <input
              type="number"
              min="0"
              max="1"
              step="0.05"
              value={minRelevance}
              onChange={(event) => setMinRelevance(Number(event.currentTarget.value))}
            />
          </label>
        </div>

        <fieldset className="source-picker">
          <legend>学术来源</legend>
          {ACADEMIC_SOURCES.map((source) => (
            <label key={source}>
              <input
                type="checkbox"
                checked={sources.includes(source)}
                onChange={(event) => {
                  // 先在 handler 内捕获 checked：函数式 updater 可能在渲染阶段
                  // 才执行，届时 event.currentTarget 已被 React 置空，
                  // 读它会抛 TypeError 触发路由 ErrorBoundary（整页跳重试页）。
                  const checked = event.currentTarget.checked;
                  setSources((current) => checked
                    ? [...current, source]
                    : current.filter((item) => item !== source));
                }}
              />
              {SOURCE_LABELS[source]}
            </label>
          ))}
        </fieldset>

        <div className="acquire-options">
          <label>
            <input type="checkbox" checked={expand} onChange={(event) => setExpand(event.currentTarget.checked)} />
            扩展检索词
          </label>
          <label>
            <input type="checkbox" checked={onlyA} onChange={(event) => setOnlyA(event.currentTarget.checked)} />
            仅 CCF-A
          </label>
          <button type="button" onClick={expandQueries} disabled={busy}>生成检索词</button>
        </div>

        {queries.length > 0 ? (
          <label className="acquire-queries">
            <span>检索词（每行一条）</span>
            <textarea
              value={queries.join('\n')}
              onChange={(event) => setQueries(event.currentTarget.value.split(/\r?\n/))}
              rows={Math.min(6, Math.max(2, queries.length))}
            />
          </label>
        ) : null}
        {expandStatus ? <p className="acquire-inline-status" aria-live="polite">{expandStatus}</p> : null}

        {history.length > 0 ? (
          <div className="acquire-history" aria-label="最近检索">
            <span>最近</span>
            {history.map((item) => (
              <button key={item} type="button" onClick={() => selectHistory(item)}>{item}</button>
            ))}
          </div>
        ) : null}

        <div className="acquire-actions">
          <button
            type="button"
            className="acquire-primary"
            onClick={() => void submitBackgroundSearch()}
            disabled={busy || submittingJob}
          >
            {submittingJob ? '提交中…' : '后台检索'}
          </button>
          <button type="button" onClick={submitSearch} disabled={busy}>
            开始检索
          </button>
          {busy ? <button type="button" onClick={stopCurrentRun}>停止接收</button> : null}
          {(state.status === 'stopped' || state.status === 'failure') && lastRequest ? (
            <button type="button" onClick={() => void executeSearch(lastRequest)}>重试检索</button>
          ) : null}
          {state.candidates.length > 0 ? (
            <>
              <button type="button" onClick={verifyCandidates} disabled={busy}>核验会议信息</button>
              <button type="button" onClick={ingestSelected} disabled={busy || state.selectedKeys.length === 0}>
                入库选中项
              </button>
            </>
          ) : null}
        </div>

        {validationErrors.length > 0 ? (
          <div className="acquire-alert" role="alert">{validationErrors.join('；')}</div>
        ) : null}
        <p className="acquire-hint">
          后台检索由服务端持续执行，离开页面不会中断，可随时回来查看进度；
          「开始检索」为流式模式，需停留在本页直到完成。
        </p>
        {statusMessage ? (
          <div className={state.status === 'failure' ? 'acquire-alert' : 'acquire-result'} role={state.status === 'failure' ? 'alert' : 'status'}>
            {statusMessage}
          </div>
        ) : null}
      </section>

      <section className="acquire-stream" aria-label="候选流">
        <header>
          <div>
            <span className="section-kicker">LIVE CANDIDATES</span>
            <h2>候选与核验</h2>
          </div>
          <span>{state.selectedKeys.length} SELECTED</span>
        </header>
        <CandidateList
          candidates={state.candidates}
          selectedKeys={state.selectedKeys}
          verifications={state.verifications}
          disabled={busy}
          onToggle={(key, selected) => dispatch({ type: 'toggle-candidate', key, selected })}
          onToggleAll={(selected) => dispatch({ type: 'select-all', selected })}
        />
        {state.progress.length > 0 ? (
          <pre className="acquire-log" aria-label="采集进度" aria-live="polite">
            {state.progress.join('\n')}
          </pre>
        ) : null}
      </section>
      </div>

      <section className="acquire-jobs" aria-label="后台任务">
        <header>
          <div>
            <span className="section-kicker">SERVER JOBS</span>
            <h2>后台任务</h2>
          </div>
          <Link to="/jobs">任务页</Link>
        </header>
        {backgroundJobsQuery.isPending ? <p className="acquire-jobs__loading">读取任务…</p> : null}
        {backgroundJobsQuery.isError ? (
          <p className="acquire-alert" role="alert">{errorMessage(backgroundJobsQuery.error)}</p>
        ) : null}
        {backgroundJobsQuery.data && backgroundJobsQuery.data.length > 0 ? (
          <ol className="acquire-jobs__list">
            {backgroundJobsQuery.data.slice(0, 8).map((job) => (
              <li key={job.id}>
                <Link to={`/jobs/${job.id}`}>
                  <span className="acquire-jobs__id">#{job.id}</span>
                  <strong>{job.query || `任务 ${job.id}`}</strong>
                  <span className={`acquire-jobs__status acquire-jobs__status--${job.status}`}>
                    {job.status}
                  </span>
                  <span className="acquire-jobs__counts">{job.added} / {job.found}</span>
                </Link>
              </li>
            ))}
          </ol>
        ) : backgroundJobsQuery.data ? (
          <p className="acquire-jobs__empty">暂无后台任务。发起「后台检索」后可随时回这里查看状态。</p>
        ) : null}
      </section>

      <LocalPdfPanel />
    </div>
  );
}

export const ErrorBoundary = RouteErrorBoundary;
