/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import type { EChartsOption } from 'echarts';

import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { citationKeys, paperKeys } from '../../lib/api/keys';
import { paperApi } from '../../lib/api/paperApi';
import type { CitationNode } from '../../lib/api/types';
import { workspaceApi } from '../../lib/api/workspaceApi';
import {
  buildCitationGraphOption,
  buildTopCitationsOption,
  buildTopicTreemapOption,
  buildVenueCompositionOption,
  buildYearTrendOption,
} from '../../lib/charts/options';
import { useEChart } from '../../lib/charts/useEChart';
import type { WorkspaceRouteHandle } from '../../lib/workspace';
import './insights.css';

export const handle = {
  title: '洞察',
  layout: 'standard',
} satisfies WorkspaceRouteHandle;

export const ErrorBoundary = RouteErrorBoundary;

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message
    : '研究洞察暂时不可用。';
}

function eventPaperId(params: unknown): string | null {
  if (!params || typeof params !== 'object') return null;
  const data = Reflect.get(params, 'data');
  if (!data || typeof data !== 'object') return null;
  const id = Reflect.get(data, 'id');
  return typeof id === 'string' && id.trim() ? id : null;
}

function ChartPanel({
  title,
  eyebrow,
  description,
  option,
  onOpenPaper,
}: {
  readonly title: string;
  readonly eyebrow: string;
  readonly description: string;
  readonly option: EChartsOption | null;
  readonly onOpenPaper?: (paperId: string) => void;
}) {
  const headingId = useId();
  const ref = useEChart({
    option,
    hasData: option !== null,
    onClick: (params) => {
      const paperId = eventPaperId(params);
      if (paperId) onOpenPaper?.(paperId);
    },
  });

  return (
    <section className="insight-panel" aria-labelledby={headingId}>
      <header>
        <p>{eyebrow}</p>
        <h3 id={headingId}>{title}</h3>
        <span>{description}</span>
      </header>
      {option ? (
        <div
          ref={ref}
          className="insight-panel__chart"
          role="img"
          aria-label={`${title}图表`}
        />
      ) : (
        <div className="insight-panel__empty" role="status">
          没有足够的真实数据生成此图表。
        </div>
      )}
    </section>
  );
}

export function Component() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const abortRef = useRef<AbortController | null>(null);
  const commandRunRef = useRef(0);
  const [progress, setProgress] = useState<string[]>([]);
  const papersQuery = useQuery({
    queryKey: paperKeys.list(),
    queryFn: ({ signal }) => paperApi.listPapers(signal),
  });
  const graphQuery = useQuery({
    queryKey: citationKeys.graph(),
    queryFn: ({ signal }) => workspaceApi.getCitationGraph(signal),
  });

  const beginCommand = () => {
    abortRef.current?.abort();
    const runId = ++commandRunRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    setProgress([]);
    return {
      signal: controller.signal,
      onEvent: (event: unknown) => {
        if (commandRunRef.current !== runId) return;
        if (
          event
          && typeof event === 'object'
          && Reflect.get(event, 'type') === 'progress'
          && typeof Reflect.get(event, 'line') === 'string'
        ) {
          const line = String(Reflect.get(event, 'line'));
          setProgress((current) => [...current.slice(-7), line]);
        }
      },
    };
  };

  const buildGraph = useMutation({
    mutationFn: () => workspaceApi.buildCitationGraph(beginCommand()),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: citationKeys.graph() });
    },
  });
  const normalizeVenues = useMutation({
    mutationFn: () => workspaceApi.normalizeVenues(beginCommand()),
    onSettled: async () => {
      await queryClient.invalidateQueries({ queryKey: paperKeys.all() });
    },
  });

  useEffect(() => () => {
    commandRunRef.current += 1;
    abortRef.current?.abort();
  }, []);

  const papers = useMemo(() => papersQuery.data ?? [], [papersQuery.data]);
  const graph = graphQuery.data;
  const yearOption = useMemo(() => buildYearTrendOption(papers), [papers]);
  const topicOption = useMemo(() => buildTopicTreemapOption(papers), [papers]);
  const venueOption = useMemo(() => buildVenueCompositionOption(papers), [papers]);
  const citationOption = useMemo(() => buildTopCitationsOption(papers), [papers]);
  const graphOption = useMemo(() => buildCitationGraphOption(graph), [graph]);
  const commandPending = buildGraph.isPending || normalizeVenues.isPending;
  const commandError = buildGraph.error ?? normalizeVenues.error;
  const openPaper = (paperId: string) => {
    void navigate(`/reader/${encodeURIComponent(paperId)}`);
  };

  if (papersQuery.isPending || graphQuery.isPending) {
    return <div className="insights-route__state" role="status">正在核对论文与引用事实…</div>;
  }
  if (papersQuery.isError || graphQuery.isError) {
    return (
      <div className="insights-route__state" role="alert">
        <strong>无法加载研究洞察</strong>
        <span>{errorMessage(papersQuery.error ?? graphQuery.error)}</span>
        <button
          type="button"
          onClick={() => void Promise.all([papersQuery.refetch(), graphQuery.refetch()])}
        >
          重试
        </button>
      </div>
    );
  }

  return (
    <section className="insights-route" aria-label="研究洞察">
      <header className="insights-route__intro">
        <div>
          <p>EVIDENCE MAP</p>
          <h2>从馆藏事实观察研究结构</h2>
          <span>所有计数、分组与引用边均来自当前论文库和服务端图谱。</span>
        </div>
        <dl className="insights-route__metrics">
          <div><dt>论文</dt><dd>{papers.length}</dd></div>
          <div><dt>引用边</dt><dd>{graph?.edgeCount ?? 0}</dd></div>
          <div><dt>图节点</dt><dd>{graph?.nodes.length ?? 0}</dd></div>
        </dl>
      </header>

      <div className="insights-route__commands">
        <button type="button" disabled={commandPending} onClick={() => buildGraph.mutate()}>
          {buildGraph.isPending ? '正在构建引用图…' : '重建引用图'}
        </button>
        <button type="button" disabled={commandPending} onClick={() => normalizeVenues.mutate()}>
          {normalizeVenues.isPending ? '正在规范场所…' : '规范发表场所'}
        </button>
        {commandError ? <output className="insights-route__command-error">{errorMessage(commandError)}</output> : null}
      </div>

      {progress.length > 0 ? (
        <ol className="insights-route__progress" aria-label="洞察命令进度" aria-live="polite">
          {progress.map((line, index) => <li key={`${index}:${line}`}>{line}</li>)}
        </ol>
      ) : null}

      <div className="insights-grid">
        <ChartPanel title="年度轨迹" eyebrow="YEAR TREND" description="按论文年份汇总，不补齐不存在的年份。" option={yearOption} />
        <ChartPanel title="主题结构" eyebrow="TOPIC TREE" description="论文类型到研究主题的真实层级。" option={topicOption} />
        <ChartPanel title="发表场所" eyebrow="VENUE MIX" description="仅统计论文记录中存在的场所。" option={venueOption} />
        <ChartPanel title="高引用论文" eyebrow="CITATION RANK" description="按服务端论文引用数字段排序。" option={citationOption} onOpenPaper={openPaper} />
        <div className="insights-grid__wide">
          <ChartPanel title="引用网络" eyebrow="CITATION GRAPH" description="有向边保持 source 引用 target 的服务端语义。" option={graphOption} onOpenPaper={openPaper} />
          {graph && graph.nodes.length > 0 ? (
            <CitationNodeList nodes={graph.nodes} onOpenPaper={openPaper} />
          ) : null}
        </div>
      </div>
    </section>
  );
}

function CitationNodeList({
  nodes,
  onOpenPaper,
}: {
  readonly nodes: readonly CitationNode[];
  readonly onOpenPaper: (paperId: string) => void;
}) {
  const sorted = [...nodes]
    .sort((left, right) => right.indeg - left.indeg || left.title.localeCompare(right.title))
    .slice(0, 12);
  return (
    <aside className="citation-node-list" aria-labelledby="citation-node-list-title">
      <h3 id="citation-node-list-title">可访问节点列表</h3>
      <ol>
        {sorted.map((node) => (
          <li key={node.id}>
            <button type="button" onClick={() => onOpenPaper(node.id)}>
              <span>{node.title}</span>
              <small>入 {node.indeg} · 出 {node.outdeg}</small>
            </button>
          </li>
        ))}
      </ol>
    </aside>
  );
}
