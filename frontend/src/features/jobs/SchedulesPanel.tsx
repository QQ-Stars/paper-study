import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { scheduleKeys } from '../../lib/api/keys';
import { workspaceApi } from '../../lib/api/workspaceApi';
import { ACADEMIC_SOURCES, normalizeSearchDraft, type AcademicSource } from '../acquire/acquireReducer';

const SOURCE_LABELS: Record<AcademicSource, string> = {
  semanticscholar: 'Semantic Scholar',
  arxiv: 'arXiv',
  openalex: 'OpenAlex',
  dblp: 'DBLP',
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function SchedulesPanel() {
  const queryClient = useQueryClient();
  const schedulesQuery = useQuery({
    queryKey: scheduleKeys.list(),
    queryFn: ({ signal }) => workspaceApi.listSchedules(signal),
  });
  const [query, setQuery] = useState('');
  const [sources, setSources] = useState<string[]>(['semanticscholar', 'arxiv']);
  const [years, setYears] = useState('2024-2026');
  const [maxPapers, setMaxPapers] = useState(10);
  const [minRelevance, setMinRelevance] = useState(0);
  const [onlyA, setOnlyA] = useState(false);
  const [everyDays, setEveryDays] = useState(7);
  const [busyId, setBusyId] = useState<number | 'create' | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => queryClient.invalidateQueries({ queryKey: scheduleKeys.list() });

  const createSchedule = async () => {
    const normalized = normalizeSearchDraft({
      query,
      sources,
      years,
      max: maxPapers,
      minRelevance,
      onlyA,
    });
    if (!normalized.ok) {
      setError(normalized.errors.join('；'));
      return;
    }
    setBusyId('create');
    setError(null);
    setStatus(null);
    try {
      const id = await workspaceApi.createSchedule({
        query: normalized.request.query,
        sources: normalized.request.sources,
        years: normalized.request.years,
        max: normalized.request.max,
        minRelevance: normalized.request.minRelevance,
        onlyA: normalized.request.onlyA,
        everyDays: Math.max(1, Math.trunc(everyDays || 7)),
      });
      await refresh();
      setStatus(`计划 ${id} 已由服务器创建。`);
      setQuery('');
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusyId(null);
    }
  };

  const toggle = async (id: number, enabled: boolean) => {
    setBusyId(id);
    setError(null);
    setStatus(null);
    try {
      await workspaceApi.toggleSchedule(id, enabled);
      await refresh();
      setStatus(`计划 ${id} 状态已由服务器确认。`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (id: number) => {
    if (!globalThis.confirm(`删除计划 ${id}？`)) return;
    setBusyId(id);
    setError(null);
    setStatus(null);
    try {
      await workspaceApi.deleteSchedule(id);
      await refresh();
      setStatus(`计划 ${id} 已删除。`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusyId(null);
    }
  };

  return (
    <section className="schedules-panel" aria-label="定时计划">
      <header>
        <div>
          <span className="jobs-kicker">SCHEDULES</span>
          <h2>定时计划</h2>
        </div>
        <strong>{schedulesQuery.data?.length ?? 0}</strong>
      </header>

      <div className="schedule-form">
        <label className="schedule-form__query">
          <span>计划研究方向</span>
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
          <span>间隔天数</span>
          <input
            type="number"
            min="1"
            value={everyDays}
            onChange={(event) => setEveryDays(Math.max(1, Number(event.currentTarget.value)))}
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
        <fieldset>
          <legend>来源</legend>
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
        <label className="schedule-form__only-a">
          <input type="checkbox" checked={onlyA} onChange={(event) => setOnlyA(event.currentTarget.checked)} />
          仅 CCF-A
        </label>
        <button type="button" onClick={createSchedule} disabled={busyId !== null}>创建计划</button>
      </div>

      {schedulesQuery.isPending ? <p>读取定时计划…</p> : null}
      {schedulesQuery.isError ? <p role="alert">{errorMessage(schedulesQuery.error)}</p> : null}
      {schedulesQuery.data?.length === 0 ? (
        <p className="schedules-panel__empty">没有定时计划。新建计划会由服务端调度器执行。</p>
      ) : null}
      {schedulesQuery.data && schedulesQuery.data.length > 0 ? (
        <ul className="schedule-list">
          {schedulesQuery.data.map((schedule) => (
            <li key={schedule.id}>
              <div>
                <strong>{schedule.query}</strong>
                <p>{schedule.sources.join(' · ')} · 每 {schedule.everyDays} 天</p>
                <small>上次：{schedule.lastRun || '尚未运行'}</small>
                <small>下次：{schedule.nextRun || '等待服务端排期'}</small>
              </div>
              <span className={schedule.enabled ? 'schedule-enabled' : 'schedule-disabled'}>
                {schedule.enabled ? '已启用' : '已停用'}
              </span>
              <div className="schedule-list__actions">
                <button
                  type="button"
                  aria-label={`${schedule.enabled ? '停用' : '启用'}计划 ${schedule.id}`}
                  disabled={busyId !== null}
                  onClick={() => toggle(schedule.id, !schedule.enabled)}
                >
                  {schedule.enabled ? '停用' : '启用'}
                </button>
                <button
                  type="button"
                  aria-label={`删除计划 ${schedule.id}`}
                  disabled={busyId !== null}
                  onClick={() => remove(schedule.id)}
                >
                  删除
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {status ? <p className="schedules-panel__status" role="status">{status}</p> : null}
      {error ? <p className="schedules-panel__error" role="alert">{error}</p> : null}
    </section>
  );
}
