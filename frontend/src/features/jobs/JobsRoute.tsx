/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata and polling policy with their component. */
import { Badge, Button, Checkbox, Input, Loader } from '@cloudflare/kumo';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';

import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { jobKeys } from '../../lib/api/keys';
import type { JobSummary } from '../../lib/api/types';
import { jobsGateway } from '../../lib/api/jobsGateway';
import type { WorkspaceRouteHandle } from '../../lib/workspace';
import {
  ACADEMIC_SOURCES,
  SOURCE_LABELS,
  normalizeSearchDraft,
} from '../../lib/research-search';
import { JobDetail } from './JobDetail';
import { SchedulesPanel } from './SchedulesPanel';
import './jobs.css';

export const handle = {
  title: '任务',
  layout: 'inspector-drawer',
} satisfies WorkspaceRouteHandle;

export function jobPollingIntervalFor(jobs: JobSummary[] | undefined): 2500 | false {
  return jobs?.some((job) => job.status === 'pending' || job.status === 'running')
    ? 2500
    : false;
}

function jobStatusBadgeVariant(status: JobSummary['status']) {
  switch (status) {
    case 'pending':
    case 'running':
      return 'warning';
    case 'review':
      return 'primary';
    case 'done':
      return 'success';
    case 'failed':
      return 'error';
    default:
      return 'outline';
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function jobYears(job: JobSummary): string {
  if (job.yearFrom === null && job.yearTo === null) return '全部年份';
  if (job.yearTo === null || job.yearTo === job.yearFrom) return String(job.yearFrom ?? job.yearTo);
  return `${job.yearFrom ?? '…'}-${job.yearTo}`;
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
    queryFn: ({ signal }) => jobsGateway.listJobs(signal),
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
      const id = await jobsGateway.createJob({
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
          <Input
            label="后台研究方向"
            className="w-full jobs-create-form__query"
            value={query}
            onChange={(event) => setQuery((event.target as HTMLInputElement).value)}
          />
          <Input
            label="年份"
            className="w-full"
            value={years}
            onChange={(event) => setYears((event.target as HTMLInputElement).value)}
          />
          <Input
            label="最多候选"
            type="number"
            min={1}
            max={60}
            className="w-full"
            value={maxPapers}
            onChange={(event) => setMaxPapers(Number((event.target as HTMLInputElement).value))}
          />
          <Input
            label="最低相关度"
            type="number"
            min={0}
            max={1}
            step={0.05}
            className="w-full"
            value={minRelevance}
            onChange={(event) => setMinRelevance(Number((event.target as HTMLInputElement).value))}
          />
        </div>
        <fieldset className="jobs-source-picker">
          <legend>学术来源</legend>
          {ACADEMIC_SOURCES.map((source) => (
            <Checkbox
              key={source}
              label={SOURCE_LABELS[source]}
              checked={sources.includes(source)}
              onCheckedChange={(checked) => setSources((current) => checked
                ? [...current, source]
                : current.filter((item) => item !== source))}
            />
          ))}
        </fieldset>
        <div className="jobs-create-actions">
          <Checkbox
            label="仅 CCF-A"
            checked={onlyA}
            onCheckedChange={(checked) => setOnlyA(checked)}
          />
          <Button type="button" variant="primary" onClick={() => void createJob()} disabled={creating}>创建后台任务</Button>
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
        {jobsQuery.isPending ? (
          <p className="jobs-list-panel__loading"><Loader size="sm" />读取任务…</p>
        ) : null}
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
                  <Badge className={`job-status job-status--${job.status}`} variant={jobStatusBadgeVariant(job.status)}>{job.status}</Badge>
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
