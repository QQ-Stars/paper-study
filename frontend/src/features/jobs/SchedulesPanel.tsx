import { Badge, Button, Checkbox, Input, Loader } from '@cloudflare/kumo';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { scheduleKeys } from '../../lib/api/keys';
import { schedulesGateway } from '../../lib/api/schedulesGateway';
import {
  ACADEMIC_SOURCES,
  SOURCE_LABELS,
  normalizeSearchDraft,
} from '../../lib/research-search';

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function SchedulesPanel() {
  const queryClient = useQueryClient();
  const schedulesQuery = useQuery({
    queryKey: scheduleKeys.list(),
    queryFn: ({ signal }) => schedulesGateway.listSchedules(signal),
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
      const id = await schedulesGateway.createSchedule({
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
      await schedulesGateway.toggleSchedule(id, enabled);
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
      await schedulesGateway.deleteSchedule(id);
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
        <Input
          label="计划研究方向"
          className="w-full schedule-form__query"
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
          label="间隔天数"
          type="number"
          min={1}
          className="w-full"
          value={everyDays}
          onChange={(event) => setEveryDays(Math.max(1, Number((event.target as HTMLInputElement).value)))}
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
        <fieldset>
          <legend>来源</legend>
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
        <Checkbox
          className="schedule-form__only-a"
          label="仅 CCF-A"
          checked={onlyA}
          onCheckedChange={(checked) => setOnlyA(checked)}
        />
        <Button type="button" variant="primary" className="schedule-form__create" onClick={() => void createSchedule()} disabled={busyId !== null}>创建计划</Button>
      </div>

      {schedulesQuery.isPending ? <p><Loader size="sm" />读取定时计划…</p> : null}
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
              <Badge
                className={schedule.enabled ? 'schedule-enabled' : 'schedule-disabled'}
                variant={schedule.enabled ? 'success' : 'neutral'}
                appearance="dot"
              >
                {schedule.enabled ? '已启用' : '已停用'}
              </Badge>
              <div className="schedule-list__actions">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-label={`${schedule.enabled ? '停用' : '启用'}计划 ${schedule.id}`}
                  disabled={busyId !== null}
                  onClick={() => void toggle(schedule.id, !schedule.enabled)}
                >
                  {schedule.enabled ? '停用' : '启用'}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  aria-label={`删除计划 ${schedule.id}`}
                  disabled={busyId !== null}
                  onClick={() => void remove(schedule.id)}
                >
                  删除
                </Button>
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
