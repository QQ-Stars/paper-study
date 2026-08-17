import { useCallback, useEffect, useState } from 'react';

import { jobApi, scheduleApi, v2Api } from '../api/client';
import type { Candidate, LegacyJob, Schedule, StreamEvent, V2JobSummary } from '../api/types';
import { PlusIcon } from './Icons';
import { StreamConsole, useStream } from './StreamConsole';

interface JobsPageProps {
  notify: (message: string) => void;
}

export function JobsPage({ notify }: JobsPageProps) {
  /* ── legacy 采集任务 ── */
  const [jobs, setJobs] = useState<LegacyJob[]>([]);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const confirmStream = useStream();

  const loadJobs = useCallback(() => {
    jobApi.list().then(setJobs).catch(() => setJobs([]));
  }, []);

  useEffect(() => {
    loadJobs();
    v2Api.listJobs().then((result) => setV2Jobs(result.items)).catch(() => setV2Jobs([]));
    scheduleApi.list().then(setSchedules).catch(() => setSchedules([]));
  }, [loadJobs]);

  const openDetail = async (id: number) => {
    try {
      setDetail(await jobApi.detail(id));
    } catch (error) {
      notify(`详情加载失败：${error instanceof Error ? error.message : error}`);
    }
  };

  const removeJob = async (id: number) => {
    await jobApi.remove(id);
    loadJobs();
    notify(`任务 #${id} 已删除`);
  };

  const confirmImport = async (jobId: number, candidates: Candidate[]) => {
    const anchor = confirmStream.anchorRef.current + 1;
    confirmStream.begin();
    try {
      await jobApi.confirm({ jobId, candidates }, (event: StreamEvent) =>
        confirmStream.accept(anchor, event),
      );
      loadJobs();
      notify('候选已确认导入');
    } catch (error) {
      confirmStream.fail(anchor, error);
    }
  };

  /* ── v2 durable 任务 ── */
  const [v2Jobs, setV2Jobs] = useState<V2JobSummary[]>([]);
  const [v2Events, setV2Events] = useState<Array<Record<string, unknown>> | null>(null);

  const loadV2Jobs = () => {
    v2Api.listJobs().then((result) => setV2Jobs(result.items)).catch(() => setV2Jobs([]));
  };

  const showEvents = async (id: string) => {
    try {
      setV2Events(await v2Api.jobEvents(id));
    } catch {
      setV2Events([]);
    }
  };

  const cancelJob = async (id: string) => {
    await v2Api.cancelJob(id).catch(() => undefined);
    loadV2Jobs();
    notify(`已请求取消 ${id.slice(0, 8)}…`);
  };

  const retryJob = async (id: string) => {
    await v2Api.retryJob(id).catch(() => undefined);
    loadV2Jobs();
    notify(`已重新入队 ${id.slice(0, 8)}…`);
  };

  /* ── 定时调度 ── */
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [scheduleDraft, setScheduleDraft] = useState({ query: '', sources: 'arxiv', years: '2024-2026', max: 10 });

  const loadSchedules = () => {
    scheduleApi.list().then(setSchedules).catch(() => setSchedules([]));
  };

  const createSchedule = async () => {
    if (!scheduleDraft.query.trim()) {
      notify('请输入定时采集的检索方向');
      return;
    }
    const result = await scheduleApi.create(scheduleDraft);
    if (result.ok) {
      notify(`定时任务已创建（#${result.id}）`);
      setScheduleDraft({ query: '', sources: 'arxiv', years: '2024-2026', max: 10 });
      loadSchedules();
    } else {
      notify(`创建失败：${result.error}`);
    }
  };

  const toggleSchedule = async (schedule: Schedule) => {
    const enabled = !(schedule.enabled === 1 || schedule.enabled === true);
    await scheduleApi.toggle(schedule.id, enabled);
    loadSchedules();
  };

  const removeSchedule = async (id: number) => {
    await scheduleApi.remove(id);
    loadSchedules();
    notify(`定时任务 #${id} 已删除`);
  };

  return (
    <div className="page page-enter jobs">
      <div className="jobs__grid">
        {/* ── legacy 采集任务 ── */}
        <section className="card jobs__panel" aria-labelledby="jobs-legacy">
          <header className="insights__panel-head">
            <h3 className="section-title" id="jobs-legacy">
              采集任务
            </h3>
            <button type="button" className="btn btn--sm" onClick={loadJobs}>
              刷新
            </button>
          </header>
          {jobs.length === 0 ? (
            <p className="artifacts__empty">暂无采集任务。可在「采集」页存为后台任务。</p>
          ) : (
            <ul className="jobs__list">
              {jobs.map((job) => (
                <li key={job.id} className="jobs__row">
                  <div className="jobs__row-copy">
                    <strong>#{job.id} {String(job.query ?? '')}</strong>
                    <small>
                      {String(job.status ?? '')} · 候选 {String(job.candidates ?? 0)} ·{' '}
                      {String(job.created_at ?? '')}
                    </small>
                  </div>
                  <div className="deep__actions">
                    <button type="button" className="btn btn--sm" onClick={() => void openDetail(job.id)}>
                      详情
                    </button>
                    <button type="button" className="btn btn--ghost btn--sm" onClick={() => void removeJob(job.id)}>
                      删除
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {detail && (
            <div className="jobs__detail">
              <h4>任务详情（GET /api/jobs/detail）</h4>
              <pre className="deep__code">{JSON.stringify(detail, null, 2).slice(0, 3000)}</pre>
              {Array.isArray(detail.candidates) && detail.candidates.length > 0 && (
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  onClick={() => void confirmImport(Number(detail.id), detail.candidates as Candidate[])}
                  disabled={confirmStream.state.running}
                >
                  确认导入全部候选
                </button>
              )}
              <StreamConsole state={confirmStream.state} />
            </div>
          )}
        </section>

        {/* ── v2 durable 任务 ── */}
        <section className="card jobs__panel" aria-labelledby="jobs-v2">
          <header className="insights__panel-head">
            <h3 className="section-title" id="jobs-v2">
              durable 处理任务
            </h3>
            <button type="button" className="btn btn--sm" onClick={loadV2Jobs}>
              刷新
            </button>
          </header>
          {v2Jobs.length === 0 ? (
            <p className="artifacts__empty">
              暂无 durable 任务。可在文献库「深度处理」页签入队源文档/AI 工件。
            </p>
          ) : (
            <ul className="jobs__list">
              {v2Jobs.map((job) => (
                <li key={job.id} className="jobs__row">
                  <div className="jobs__row-copy">
                    <strong>{job.type}</strong>
                    <small>
                      {job.status} · {String(job.id).slice(0, 14)}… · {String(job.updatedAt ?? '')}
                    </small>
                  </div>
                  <div className="deep__actions">
                    <button type="button" className="btn btn--sm" onClick={() => void showEvents(job.id)}>
                      事件
                    </button>
                    <button type="button" className="btn btn--sm" onClick={() => void retryJob(job.id)}>
                      重试
                    </button>
                    <button type="button" className="btn btn--ghost btn--sm" onClick={() => void cancelJob(job.id)}>
                      取消
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {v2Events && (
            <div className="jobs__detail">
              <h4>任务事件流（GET /api/v2/jobs/&#123;id&#125;/events）</h4>
              <pre className="deep__code">{JSON.stringify(v2Events, null, 2).slice(0, 3000)}</pre>
            </div>
          )}
        </section>

        {/* ── 定时调度 ── */}
        <section className="card jobs__panel jobs__panel--wide" aria-labelledby="jobs-schedules">
          <header className="insights__panel-head">
            <h3 className="section-title" id="jobs-schedules">
              定时采集调度
            </h3>
            <button type="button" className="btn btn--sm" onClick={loadSchedules}>
              刷新
            </button>
          </header>
          <div className="reviews__start-row manage__schedule-form">
            <input
              className="input"
              placeholder="检索方向…"
              aria-label="定时检索方向"
              value={scheduleDraft.query}
              onChange={(event) => setScheduleDraft((prev) => ({ ...prev, query: event.target.value }))}
            />
            <input
              className="input"
              placeholder="来源（逗号分隔）"
              aria-label="数据源"
              value={scheduleDraft.sources}
              onChange={(event) => setScheduleDraft((prev) => ({ ...prev, sources: event.target.value }))}
            />
            <input
              className="input"
              placeholder="年份"
              aria-label="年份范围"
              value={scheduleDraft.years}
              onChange={(event) => setScheduleDraft((prev) => ({ ...prev, years: event.target.value }))}
            />
            <button type="button" className="btn btn--primary" onClick={() => void createSchedule()}>
              <PlusIcon size={14} />
              创建
            </button>
          </div>
          {schedules.length === 0 ? (
            <p className="artifacts__empty">暂无定时任务。</p>
          ) : (
            <ul className="jobs__list">
              {schedules.map((schedule) => {
                const enabled = schedule.enabled === 1 || schedule.enabled === true;
                return (
                  <li key={schedule.id} className="jobs__row">
                    <div className="jobs__row-copy">
                      <strong>
                        #{schedule.id} {schedule.query}
                        <span className={`badge ${enabled ? 'badge--jade' : 'badge--venue'}`}>
                          {enabled ? '启用' : '停用'}
                        </span>
                      </strong>
                      <small>
                        来源 {schedule.sources} · 年份 {schedule.years} · 上限 {schedule.max} · 累计入库{' '}
                        {schedule.added ?? 0}
                      </small>
                    </div>
                    <div className="deep__actions">
                      <button type="button" className="btn btn--sm" onClick={() => void toggleSchedule(schedule)}>
                        {enabled ? '停用' : '启用'}
                      </button>
                      <button type="button" className="btn btn--ghost btn--sm" onClick={() => void removeSchedule(schedule.id)}>
                        删除
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
