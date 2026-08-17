/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import { Button, Input, Select, Surface } from '@cloudflare/kumo';
import { useEffect, useId, useMemo, useReducer, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import type { EChartsOption } from 'echarts';

import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { isAbortError } from '../../lib/api/errors';
import { citationKeys, paperKeys } from '../../lib/api/keys';
import { paperApi } from '../../lib/api/paperApi';
import type { CitationNode } from '../../lib/api/types';
import { insightsGateway } from '../../lib/api/insightsGateway';
import {
  buildCitationGraphOption,
  buildTopCitationsOption,
  buildTopicTreemapOption,
  buildVenueCompositionOption,
  buildYearTrendOption,
} from '../../lib/charts/options';
import { useEChart } from '../../lib/charts/useEChart';
import type { WorkspaceRouteHandle } from '../../lib/workspace';
import {
  createInsightsCommandSession,
  insightsCommandReducer,
  type InsightsCommand,
  type InsightsCommandTerminal,
} from './insightsCommandSession';
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

interface CommandOwner {
  readonly runId: number;
  readonly command: InsightsCommand;
  readonly controller: AbortController;
}

interface CommandOptions {
  readonly signal: AbortSignal;
  readonly onEvent: (event: unknown) => void;
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
    <Surface as="section" className="insight-panel" aria-labelledby={headingId}>
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
    </Surface>
  );
}

export function Component() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const ownerRef = useRef<CommandOwner | null>(null);
  const runSequence = useRef(0);
  const [session, dispatch] = useReducer(
    insightsCommandReducer,
    undefined,
    createInsightsCommandSession,
  );
  const [selectedRecommendationId, setSelectedRecommendationId] = useState('');
  const [semanticQuery, setSemanticQuery] = useState('');
  const papersQuery = useQuery({
    queryKey: paperKeys.list(),
    queryFn: ({ signal }) => paperApi.listPapers(signal),
  });
  const graphQuery = useQuery({
    queryKey: citationKeys.graph(),
    queryFn: ({ signal }) => insightsGateway.getCitationGraph(signal),
  });

  const beginCommand = (command: InsightsCommand): CommandOwner => {
    ownerRef.current?.controller.abort();
    const owner = {
      runId: ++runSequence.current,
      command,
      controller: new AbortController(),
    };
    ownerRef.current = owner;
    dispatch({ type: 'started', runId: owner.runId, command });
    return owner;
  };

  const commandOptions = (owner: CommandOwner): CommandOptions => ({
    signal: owner.controller.signal,
    onEvent: (event: unknown) => {
      if (ownerRef.current !== owner) return;
      if (
        event
        && typeof event === 'object'
        && Reflect.get(event, 'type') === 'progress'
        && typeof Reflect.get(event, 'line') === 'string'
      ) {
        dispatch({
          type: 'progressed',
          runId: owner.runId,
          line: String(Reflect.get(event, 'line')),
        });
      }
    },
  });

  const runCommand = async <T,>(
    command: InsightsCommand,
    request: (options: CommandOptions) => Promise<T>,
    terminalFor: (result: T) => InsightsCommandTerminal,
    reconcile?: () => Promise<unknown>,
  ) => {
    const owner = beginCommand(command);
    try {
      const result = await request(commandOptions(owner));
      if (ownerRef.current !== owner) return;
      dispatch({
        type: 'completed',
        runId: owner.runId,
        terminal: terminalFor(result),
      });
    } catch (error) {
      if (ownerRef.current !== owner) return;
      if (isAbortError(error)) {
        dispatch({ type: 'stopped', runId: owner.runId });
      } else {
        dispatch({ type: 'failed', runId: owner.runId, error: errorMessage(error) });
      }
    } finally {
      try {
        await reconcile?.();
      } finally {
        if (ownerRef.current === owner) ownerRef.current = null;
      }
    }
  };

  const stopCommand = () => {
    const owner = ownerRef.current;
    if (!owner) return;
    ownerRef.current = null;
    owner.controller.abort();
    dispatch({ type: 'stopped', runId: owner.runId });
  };

  useEffect(() => () => {
    runSequence.current += 1;
    const owner = ownerRef.current;
    ownerRef.current = null;
    owner?.controller.abort();
  }, []);

  const papers = useMemo(() => papersQuery.data ?? [], [papersQuery.data]);
  const papersById = useMemo(
    () => new Map(papers.map((paper) => [paper.id, paper])),
    [papers],
  );
  const recommendationPaperId = papers.some((paper) => paper.id === selectedRecommendationId)
    ? selectedRecommendationId
    : papers[0]?.id ?? '';

  const buildGraph = () => runCommand(
    'citation-build',
    (options) => insightsGateway.buildCitationGraph(options),
    (terminal) => ({
      command: 'citation-build',
      summary: `引用图已更新：${terminal.nodes} 个节点，${terminal.edges} 条边。`,
    }),
    () => queryClient.invalidateQueries({ queryKey: citationKeys.graph() }),
  );

  const normalizeVenues = () => runCommand(
    'normalize-venues',
    (options) => insightsGateway.normalizeVenues(options),
    (terminal) => ({
      command: 'normalize-venues',
      summary: `已规范 ${terminal.changed} 条发表场所记录。`,
    }),
    () => queryClient.invalidateQueries({ queryKey: paperKeys.all() }),
  );

  const recommend = () => {
    if (!recommendationPaperId) return Promise.resolve();
    return runCommand(
      'recommend',
      (options) => insightsGateway.recommend(recommendationPaperId, 14, options),
      (terminal) => ({
        command: 'recommend',
        summary: `找到 ${terminal.candidates.length} 篇真实推荐。`,
        candidates: terminal.candidates,
      }),
    );
  };

  const embed = () => runCommand(
    'embed',
    (options) => insightsGateway.embed('missing', options),
    (terminal) => ({
      command: 'embed',
      summary: `向量索引完成：${terminal.indexed} / ${terminal.total}。`,
    }),
  );

  const semanticSearch = () => {
    const query = semanticQuery.trim();
    if (!query) return Promise.resolve();
    return runCommand(
      'semantic-search',
      (options) => insightsGateway.semanticSearch(query, 60, options),
      (terminal) => {
        const results = terminal.results.filter((result) => papersById.has(result.id));
        return {
          command: 'semantic-search',
          summary: `找到 ${results.length} 篇馆藏语义匹配。`,
          query,
          results,
        };
      },
    );
  };

  const semanticMatches = session.terminal?.command === 'semantic-search'
    ? session.terminal.results.flatMap((result) => {
        const paper = papersById.get(result.id);
        return paper ? [{ paper, score: result.score }] : [];
      })
    : [];

  const graph = graphQuery.data;
  const yearOption = useMemo(() => buildYearTrendOption(papers), [papers]);
  const topicOption = useMemo(() => buildTopicTreemapOption(papers), [papers]);
  const venueOption = useMemo(() => buildVenueCompositionOption(papers), [papers]);
  const citationOption = useMemo(() => buildTopCitationsOption(papers), [papers]);
  const graphOption = useMemo(() => buildCitationGraphOption(graph), [graph]);
  const commandPending = session.phase === 'running';
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
        <Button
          type="button"
          variant="outline"
          onClick={() => void Promise.all([papersQuery.refetch(), graphQuery.refetch()])}
        >
          重试
        </Button>
      </div>
    );
  }

  return (
    <Surface as="section" className="insights-route" aria-label="研究洞察">
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
        <Button type="button" variant="outline" disabled={commandPending} onClick={() => void buildGraph()}>
          {commandPending && session.command === 'citation-build' ? '正在构建引用图…' : '重建引用图'}
        </Button>
        <Button type="button" variant="outline" disabled={commandPending} onClick={() => void normalizeVenues()}>
          {commandPending && session.command === 'normalize-venues' ? '正在规范场所…' : '规范发表场所'}
        </Button>
        <Select
          label="推荐种子论文"
          className="insights-route__recommendation-seed"
          value={recommendationPaperId}
          disabled={commandPending || papers.length === 0}
          onValueChange={(value) => setSelectedRecommendationId(value ?? '')}
        >
          {papers.map((paper) => <Select.Option key={paper.id} value={paper.id}>{paper.title}</Select.Option>)}
        </Select>
        <Button
          type="button"
          variant="outline"
          disabled={commandPending || !recommendationPaperId}
          onClick={() => void recommend()}
        >
          {commandPending && session.command === 'recommend' ? '正在推荐…' : '推荐相似论文'}
        </Button>
        <Button type="button" variant="outline" disabled={commandPending} onClick={() => void embed()}>
          {commandPending && session.command === 'embed' ? '正在更新向量…' : '更新缺失向量'}
        </Button>
        <Input
          label="语义查询"
          type="search"
          className="insights-route__semantic-query w-full"
          value={semanticQuery}
          disabled={commandPending}
          onChange={(event) => setSemanticQuery((event.target as HTMLInputElement).value)}
        />
        <Button
          type="button"
          variant="outline"
          disabled={commandPending || !semanticQuery.trim()}
          onClick={() => void semanticSearch()}
        >
          {commandPending && session.command === 'semantic-search' ? '正在语义搜索…' : '语义搜索'}
        </Button>
        {commandPending ? <Button type="button" variant="ghost" onClick={stopCommand}>停止接收</Button> : null}
      </div>

      {session.phase === 'failure' && session.error ? (
        <output className="insights-route__command-error">{session.error}</output>
      ) : null}
      {session.phase === 'stopped' ? (
        <output className="insights-route__command-status">已停止接收；服务端可能仍在运行。</output>
      ) : null}
      {session.phase === 'success' && session.terminal ? (
        <output className="insights-route__command-status">{session.terminal.summary}</output>
      ) : null}

      {session.progress.length > 0 ? (
        <ol className="insights-route__progress" aria-label="洞察命令进度" aria-live="polite">
          {session.progress.map((line, index) => <li key={`${index}:${line}`}>{line}</li>)}
        </ol>
      ) : null}

      {session.terminal?.command === 'recommend' ? (
        <section className="insights-route__recommendations" aria-labelledby="insights-recommendations-title">
          <header>
            <p>RECOMMENDATIONS</p>
            <h3 id="insights-recommendations-title">相似论文</h3>
          </header>
          {session.terminal.candidates.length > 0 ? (
            <ol>
              {session.terminal.candidates.map((candidate) => (
                <li key={`${candidate.source}:${candidate.sourceId}`}>
                  <strong>{candidate.title}</strong>
                  <span>{[
                    candidate.venue,
                    candidate.year,
                    candidate.relevance == null ? null : `相关度 ${candidate.relevance.toFixed(2)}`,
                  ].filter(Boolean).join(' · ')}</span>
                </li>
              ))}
            </ol>
          ) : <p>当前论文没有返回真实推荐结果。</p>}
        </section>
      ) : null}

      {session.terminal?.command === 'semantic-search' ? (
        <section className="insights-route__recommendations" aria-labelledby="insights-semantic-results-title">
          <header>
            <p>SEMANTIC SEARCH</p>
            <h3 id="insights-semantic-results-title">
              “{session.terminal.query}”的语义匹配
            </h3>
          </header>
          {semanticMatches.length > 0 ? (
            <ol>
              {semanticMatches.map(({ paper, score }) => (
                <li key={paper.id}>
                  <Button type="button" variant="ghost" onClick={() => openPaper(paper.id)}>
                    <strong>{paper.title}</strong>
                  </Button>
                  <span>语义得分 {score.toFixed(3)}</span>
                </li>
              ))}
            </ol>
          ) : <p>当前查询没有返回馆藏中的真实论文。</p>}
        </section>
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
    </Surface>
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
