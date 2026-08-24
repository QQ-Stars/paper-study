import { useCallback, useEffect, useState } from 'react';

import { jobApi, scheduleApi, v2Api } from '../api/client';
import type {
  Candidate,
  LegacyJob,
  Schedule,
  StreamEvent,
  V2JobDetail,
  V2JobEvent,
} from '../api/types';
import { PlusIcon } from './Icons';
import { appendJobEvents, canCancelJob, canRetryJob } from './jobHistory';
import { StreamConsole, useStream } from './StreamConsole';

interface JobsPageProps {
  notify: (message: string) => void;
}

function formatJobProgress(progress: Record<string, unknown>): string {
  const entries = Object.entries(progress);
  if (entries.length === 0) return '暂无';
  return entries
    .slice(0, 3)
    .map(([key, value]) => `${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`)
    .join(' · ');
}

function toJobErrorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function JobsPage({ notify }: JobsPageProps) {
  /* ── legacy 采集任务 ── */
  const [jobs, setJobs] = useState<LegacyJob[]>([]);
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const confirmStream = useStream();

  /* ── v2 durable 任务 ── */
  const [v2Jobs, setV2Jobs] = useState<V2JobDetail[]>([]);
  const [v2JobsLoading, setV2JobsLoading] = useState(true);
  const [v2JobsError, setV2JobsError] = useState<string | null>(null);
  const [selectedV2JobId, setSelectedV2JobId] = useState<string | null>(null);
  const [v2Detail, setV2Detail] = useState<V2JobDetail | null>(null);
  const [v2DetailLoading, setV2DetailLoading] = useState(false);
  const [v2DetailError, setV2DetailError] = useState<string | null>(null);
  const [v2EventError, setV2EventError] = useState<string | null>(null);
  const [v2EventPages, setV2EventPages] = useState<Record<string, { items: V2JobEvent[]; cursor: number }>>({});
  const [v2DetailRefresh, setV2DetailRefresh] = useState(0);
  const [v2Action, setV2Action] = useState<{ id: string; kind: 'cancel' | 'retry' } | null>(null);
  const [v2ActionError, setV2ActionError] = useState<string | null>(null);

  const loadJobs = useCallback(() => {
    jobApi.list().then(setJobs).catch(() => setJobs([]));
  }, []);

  useEffect(() => {
    loadJobs();
    void loadV2Jobs();
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

  const loadV2Jobs = async () => {
    setV2JobsLoading(true);
    setV2JobsError(null);
    try {
      const result = await v2Api.listJobs();
      setV2Jobs(result.items);
      setSelectedV2JobId((current) => {
        if (current && !result.items.some((job) => job.id === current)) return null;
        return current;
      });
    } catch (error) {
      setV2JobsError(error instanceof Error ? error.message : String(error));
    } finally {
      setV2JobsLoading(false);
    }
  };

  useEffect(() => {
    if (!selectedV2JobId) {
      setV2Detail(null);
      setV2DetailError(null);
      setV2EventError(null);
      return;
    }
    let active = true;
    const savedEvents = v2EventPages[selectedV2JobId] ?? { items: [], cursor: 0 };
    setV2DetailLoading(true);
    setV2DetailError(null);
    setV2EventError(null);
    void Promise.allSettled([
      v2Api.getJob(selectedV2JobId),
      v2Api.jobEvents(selectedV2JobId, savedEvents.cursor),
    ])
      .then(([jobResult, eventResult]) => {
        if (!active) return;
        if (jobResult.status === 'fulfilled') {
          setV2Detail(jobResult.value);
        } else {
          setV2DetailError(toJobErrorMessage(jobResult.reason));
        }
        if (eventResult.status === 'fulfilled') {
          setV2EventPages((previous) => {
            const current = previous[selectedV2JobId] ?? { items: [], cursor: 0 };
            return {
              ...previous,
              [selectedV2JobId]: {
                items: appendJobEvents(current.items, eventResult.value.items),
                cursor: Math.max(current.cursor, eventResult.value.nextAfterSequence),
              },
            };
          });
        } else {
          setV2EventError(toJobErrorMessage(eventResult.reason));
        }
      })
      .finally(() => {
        if (active) setV2DetailLoading(false);
      });
    return () => {
      active = false;
    };
  }, [selectedV2JobId, v2DetailRefresh]);

  const openV2Job = (id: string) => {
    setV2Detail(null);
    setV2DetailError(null);
    setV2EventError(null);
    setSelectedV2JobId(id);
    setV2DetailRefresh((value) => value + 1);
  };

  const performV2Action = async (id: string, kind: 'cancel' | 'retry') => {
    setV2Action({ id, kind });
    setV2ActionError(null);
    try {
      if (kind === 'cancel') {
        const job = await v2Api.cancelJob(id);
        setV2Jobs((previous) => previous.map((item) => (item.id === id ? job : item)));
        notify(`已请求取消 ${id.slice(0, 8)}…`);
      } else {
        const result = await v2Api.retryJob(id);
        await loadV2Jobs();
        openV2Job(result.job.id);
        notify(`已重新入队 ${result.job.id.slice(0, 8)}…`);
        return;
      }
      await loadV2Jobs();
      if (kind === 'cancel' && selectedV2JobId === id) setV2DetailRefresh((value) => value + 1);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setV2ActionError(message);
      notify(`${kind === 'cancel' ? '取消' : '重试'}失败：${message}`);
    } finally {
      setV2Action(null);
    }
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
            <button type="button" className="btn btn--sm" onClick={() => void loadV2Jobs()} disabled={v2JobsLoading}>
              {v2JobsLoading ? '加载中…' : '刷新'}
            </button>
          </header>
          {v2JobsError ? (
            <div className="jobs__detail" role="alert">
              <p className="deep__fact">任务列表加载失败：{v2JobsError}</p>
              <button type="button" className="btn btn--sm" onClick={() => void loadV2Jobs()}>
                重试
              </button>
            </div>
          ) : v2JobsLoading ? (
            <p className="artifacts__empty" role="status">正在加载 durable 任务…</p>
          ) : v2Jobs.length === 0 ? (
            <p className="artifacts__empty">
              暂无 durable 任务。可在文献库「深度处理」页签入队源文档/AI 工件。
            </p>
          ) : (
            <ul className="jobs__list">
              {v2Jobs.map((job) => (
                <li key={job.id} className="jobs__row">
                  <div className="jobs__row-copy">
                    <strong>{job.jobType}</strong>
                    <small>
                      {job.status} · {String(job.id).slice(0, 14)}… · {job.createdAt}
                    </small>
                  </div>
                  <div className="deep__actions">
                    <button
                      type="button"
                      className="btn btn--sm"
                      onClick={() => openV2Job(job.id)}
                      aria-label={`查看 ${job.jobType} 任务详情和事件`}
                      aria-pressed={selectedV2JobId === job.id}
                    >
                      {selectedV2JobId === job.id ? '详情中' : '事件'}
                    </button>
                    <button
                      type="button"
                      className="btn btn--sm"
                      onClick={() => void performV2Action(job.id, 'retry')}
                      disabled={!canRetryJob(job.status) || (v2Action?.id === job.id)}
                      aria-label={`重试 ${job.jobType} 任务`}
                    >
                      重试
                    </button>
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => void performV2Action(job.id, 'cancel')}
                      disabled={!canCancelJob(job.status) || (v2Action?.id === job.id)}
                      aria-label={`取消 ${job.jobType} 任务`}
                    >
                      取消
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {v2ActionError && (
            <div className="jobs__detail" role="alert">
              <p className="deep__fact">任务操作失败：{v2ActionError}</p>
              <button type="button" className="btn btn--sm" onClick={() => setV2ActionError(null)}>
                关闭提示
              </button>
            </div>
          )}
          {selectedV2JobId && v2Detail && (
            <div className="jobs__detail">
              <div className="insights__panel-head">
                <h4>任务详情</h4>
                <button
                  type="button"
                  className="btn btn--sm"
                  onClick={() => setV2DetailRefresh((value) => value + 1)}
                  disabled={v2DetailLoading}
                >
                  {v2DetailLoading ? '刷新中…' : '刷新详情'}
                </button>
              </div>
              <p className="deep__fact">
                类型 {v2Detail.jobType} · 状态 {v2Detail.status} · Paper {v2Detail.paperId ?? '无'}
              </p>
              <p className="deep__fact">
                来源模式 {v2Detail.sourceMode ?? '无'} · 尝试 {v2Detail.attempt}/{v2Detail.maxAttempts}
              </p>
              <p className="deep__fact">进度 {formatJobProgress(v2Detail.progress)}</p>
              {v2Detail.error && (
                <p className="deep__fact" role="alert">
                  错误 {v2Detail.error.code}{v2Detail.error.message ? `：${v2Detail.error.message}` : ''}
                </p>
              )}
              {v2DetailError && (
                <p className="deep__fact" role="alert">
                  详情刷新失败：{v2DetailError}
                </p>
              )}
              <div className="deep__block">
                <div className="insights__panel-head">
                  <h4>事件流</h4>
                  <span className="deep__fact" role="status">
                    已读至 #{v2EventPages[selectedV2JobId]?.cursor ?? 0}
                  </span>
                </div>
                {v2DetailLoading && <p className="deep__fact" role="status">正在同步任务事件…</p>}
                {v2EventError && (
                  <div role="alert">
                    <p className="deep__fact">事件同步失败：{v2EventError}</p>
                    <button
                      type="button"
                      className="btn btn--sm"
                      onClick={() => setV2DetailRefresh((value) => value + 1)}
                      disabled={v2DetailLoading}
                    >
                      重试事件
                    </button>
                  </div>
                )}
                {!v2DetailLoading && !v2EventError && (v2EventPages[selectedV2JobId]?.items.length ?? 0) === 0 && (
                  <p className="deep__fact">暂无事件记录。</p>
                )}
                {(v2EventPages[selectedV2JobId]?.items.length ?? 0) > 0 && (
                  <ol className="deep__list">
                    {v2EventPages[selectedV2JobId].items.map((event) => (
                      <li key={event.sequence}>
                        <strong>#{event.sequence} {event.type}</strong>
                        <span>{formatJobProgress(event.progress)}</span>
                        {event.error && (
                          <span>错误 {event.error.code}{event.error.message ? `：${event.error.message}` : ''}</span>
                        )}
                        <small>{event.createdAt}</small>
                      </li>
                    ))}
                  </ol>
                )}
              </div>
            </div>
          )}
          {selectedV2JobId && !v2Detail && v2DetailLoading && (
            <p className="jobs__detail" role="status">正在加载任务详情…</p>
          )}
          {selectedV2JobId && !v2Detail && !v2DetailLoading && v2DetailError && (
            <div className="jobs__detail" role="alert">
              <p className="deep__fact">任务详情加载失败：{v2DetailError}</p>
              <button type="button" className="btn btn--sm" onClick={() => setV2DetailRefresh((value) => value + 1)}>
                重试详情
              </button>
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
