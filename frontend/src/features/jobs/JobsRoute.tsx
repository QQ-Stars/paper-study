/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata and polling policy with their component. */
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import type { WorkspaceRouteHandle } from '../../app/routeHandle';
import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { jobKeys } from '../../lib/api/keys';
import type { JobSummary } from '../../lib/api/types';
import { workspaceApi } from '../../lib/api/workspaceApi';
import { ACADEMIC_SOURCES, normalizeSearchDraft, type AcademicSource } from '../acquire/acquireReducer';
import { JobDetail } from './JobDetail';
import { SchedulesPanel } from './SchedulesPanel';
import './jobs.css';

export const handle = {
  title: '任务',
  layout: 'inspector-drawer',
} satisfies WorkspaceRouteHandle;

const SOURCE_LABELS: Record<AcademicSource, string> = {
  semanticscholar: 'Semantic Scholar',
  arxiv: 'arXiv',
  openalex: 'OpenAlex',
  dblp: 'DBLP',
};

export function jobPollingIntervalFor(jobs: JobSummary[] | undefined): 2500 | false {
  return jobs?.some((job) => job.status === 'pending' || job.status === 'running')
    ? 2500
    : false;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function jobYears(job: JobSummary): string {
  if (job.yearFrom === null && job.yearTo === null) return '全部年份';
  if (job.yearTo === null || job.yearTo === job.yearFrom) return String(job.yearFrom ?? job.yearTo);
  return `${job.yearFrom ?? '…'}–${job.yearTo}`;
}

export function Component() {
  const { jobId: routeJobId } = useParams<{ jobId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [query, setQuery] = useState('');
  const [sources, setSources] = useState<string[]>(['semanticscholar', 'arxiv']);
  const [years, setYears] = useState('2024-2026');
  const [maxPapers, setMaxPapers] = useState(10);
  const [minRelevance, setMinRelevance] = useState(0);
  const [onlyA, setOnlyA] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<string | null>(null);
  const createOwnerRef = useRef<AbortController | null>(null);

  const jobsQuery = useQuery({
    queryKey: jobKeys.list(),
    queryFn: ({ signal }) => workspaceApi.listJobs(signal),
    refetchInterval: (queryState) => jobPollingIntervalFor(queryState.state.data),
  });

  useEffect(() => () => {
    createOwnerRef.current?.abort();
    createOwnerRef.current = null;
  }, []);

  const createJob = async () => {
    const normalized = normalizeSearchDraft({
      query,
      sources,
      years,
      max: maxPapers,
      minRelevance,
      onlyA,
    });
    if (!normalized.ok) {
      setCreateError(normalized.errors.join('；'));
      return;
    }
    createOwnerRef.current?.abort();
    const controller = new AbortController();
    createOwnerRef.current = controller;
    setCreating(true);
    setCreateError(null);
    try {
      const id = await workspaceApi.createJob({
        query: normalized.request.query,
        sources: normalized.request.sources,
        years: normalized.request.years,
        max: normalized.request.max,
        minRelevance: normalized.request.minRelevance,
        onlyA: normalized.request.onlyA,
      }, controller.signal);
      if (createOwnerRef.current !== controller) return;
      await queryClient.invalidateQueries({ queryKey: jobKeys.list() });
      navigate(`/jobs/${id}`);
    } catch (caught) {
      if (createOwnerRef.current !== controller) return;
      setCreateError(errorMessage(caught));
    } finally {
      if (createOwnerRef.current === controller) {
        createOwnerRef.current = null;
        setCreating(false);
      }
    }
  };

  const parsedJobId = routeJobId === undefined || routeJobId === ''
    ? null
    : Number(routeJobId);
  const validJobId = parsedJobId !== null
    && Number.isInteger(parsedJobId)
    && parsedJobId > 0
    ? parsedJobId
    : null;
  const invalidJobId = routeJobId !== undefined && validJobId === null;

  return (
    <div className="jobs-workspace">
      <section className="jobs-command" aria-label="创建后台任务">
        <header>
          <div>
            <span className="jobs-kicker">BACKGROUND INTAKE</span>
            <h2>后台采集</h2>
            <p>任务由服务端执行；离开此页面不会终止任务。</p>
          </div>
        </header>
        <div className="jobs-create-form">
          <label className="jobs-create-form__query">
            <span>后台研究方向</span>
            <input value={query} onChange={(event) => setQuery(event.currentTarget.value)} />
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
              value={maxPapers}
              onChange={(event) => setMaxPapers(Number(event.currentTarget.value))}
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
        <fieldset className="jobs-source-picker">
          <legend>学术来源</legend>
          {ACADEMIC_SOURCES.map((source) => (
            <label key={source}>
              <input
                type="checkbox"
                checked={sources.includes(source)}
                onChange={(event) => setSources((current) => event.currentTarget.checked
                  ? [...current, source]
                  : current.filter((item) => item !== source))}
              />
              {SOURCE_LABELS[source]}
            </label>
          ))}
        </fieldset>
        <div className="jobs-create-actions">
          <label>
            <input type="checkbox" checked={onlyA} onChange={(event) => setOnlyA(event.currentTarget.checked)} />
            仅 CCF-A
          </label>
          <button type="button" onClick={createJob} disabled={creating}>创建后台任务</button>
        </div>
        {createError ? <p className="jobs-error" role="alert">{createError}</p> : null}
      </section>

      <section className="jobs-list-panel" aria-label="后台任务列表">
        <header>
          <div>
            <span className="jobs-kicker">SERVER JOBS</span>
            <h2>任务队列</h2>
          </div>
          <strong>{jobsQuery.data?.length ?? 0}</strong>
        </header>
        {jobsQuery.isPending ? <p className="jobs-list-panel__loading">读取任务…</p> : null}
        {jobsQuery.isError ? <p className="jobs-error" role="alert">{errorMessage(jobsQuery.error)}</p> : null}
        {jobsQuery.data?.length === 0 ? (
          <div className="jobs-zero">
            <strong className="jobs-zero__count">0</strong>
            <h3>当前没有后台任务</h3>
            <p>这里不会生成示例任务。创建后会显示服务端真实状态。</p>
          </div>
        ) : null}
        {jobsQuery.data && jobsQuery.data.length > 0 ? (
          <ol className="jobs-list">
            {jobsQuery.data.map((job) => (
              <li key={job.id}>
                <Link
                  to={`/jobs/${job.id}`}
                  aria-current={validJobId === job.id ? 'page' : undefined}
                >
                  <span className="jobs-list__id">#{job.id}</span>
                  <span className="jobs-list__main">
                    <strong>{job.query || `任务 ${job.id}`}</strong>
                    <small>{jobYears(job)} · {job.sources.join(' · ')}</small>
                  </span>
                  <span className={`job-status job-status--${job.status}`}>{job.status}</span>
                  <span className="jobs-list__counts">{job.added} / {job.found}</span>
                </Link>
              </li>
            ))}
          </ol>
        ) : null}
      </section>

      {invalidJobId ? <section className="job-detail"><p role="alert">无效的任务编号。</p></section> : null}
      {validJobId !== null ? <JobDetail jobId={validJobId} /> : null}
      <SchedulesPanel />
    </div>
  );
}

export const ErrorBoundary = RouteErrorBoundary;
