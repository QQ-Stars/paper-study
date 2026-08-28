import { useCallback, useEffect, useMemo, useState } from 'react';

import { acquireApi } from '../api/client';
import type { Candidate, CiteGraph, Paper, StreamEvent } from '../api/types';
import { PlusIcon, SparkIcon } from './Icons';
import { StreamConsole, useStream } from './StreamConsole';

interface InsightsPageProps {
  papers: Paper[];
  notify: (message: string) => void;
  reloadPapers: () => Promise<void>;
}

export function InsightsPage({ papers, notify, reloadPapers }: InsightsPageProps) {
  const [graph, setGraph] = useState<CiteGraph | null>(null);
  const [seed, setSeed] = useState('');
  const [recommendations, setRecommendations] = useState<Candidate[]>([]);
  const buildStream = useStream();
  const recommendStream = useStream();
  const ingestStream = useStream();

  const loadGraph = useCallback(async () => {
    const nextGraph = await acquireApi.citeGraph();
    setGraph(nextGraph);
    return nextGraph;
  }, []);

  useEffect(() => {
    void loadGraph().catch(() => setGraph(null));
  }, [loadGraph]);

  const topicBars = useMemo(() => {
    const counts = new Map<string, number>();
    for (const paper of papers) {
      const topic = paper.topic || '未分类';
      counts.set(topic, (counts.get(topic) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8);
  }, [papers]);

  const yearBars = useMemo(() => {
    const counts = new Map<string, number>();
    for (const paper of papers) {
      counts.set(paper.year, (counts.get(paper.year) ?? 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [papers]);

  const topCited = useMemo(
    () => [...papers].sort((a, b) => (b.citations ?? 0) - (a.citations ?? 0)).slice(0, 6),
    [papers],
  );

  const hubNodes = useMemo(() => {
    if (!graph) return [];
    return [...(graph.nodes ?? [])]
      .sort((a, b) => (b.indeg ?? 0) + (b.outdeg ?? 0) - ((a.indeg ?? 0) + (a.outdeg ?? 0)))
      .slice(0, 6);
  }, [graph]);

  const buildGraph = async () => {
    const anchor = buildStream.anchorRef.current + 1;
    buildStream.begin();
    let terminal: StreamEvent | undefined;
    try {
      await acquireApi.buildCiteGraph((event: StreamEvent) => {
        buildStream.accept(anchor, event);
        if (event.type === 'done' || event.type === 'result') terminal = event;
      });
      if (!terminal) throw new Error('图谱重建未返回完成状态');
      if (terminal.ok === false) {
        notify(`图谱重建失败：${String(terminal.error || '引用图谱重建失败')}`);
        return;
      }
      const nextGraph = await loadGraph();
      const edgeCount = nextGraph.edgeCount ?? nextGraph.links.length;
      const failed = typeof terminal.failed === 'number' ? terminal.failed : 0;
      notify(
        edgeCount === 0
          ? `图谱已重建：${nextGraph.nodes.length} 个节点，未发现库内引用边。`
          : `图谱已重建：${nextGraph.nodes.length} 个节点 / ${edgeCount} 条引用边${
              failed > 0 ? `，${failed} 篇未匹配` : ''
            }。`,
      );
    } catch (error) {
      buildStream.fail(anchor, error);
      notify(`图谱重建失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const runRecommend = async () => {
    if (!seed.trim()) {
      notify('请先选择种子论文');
      return;
    }
    const anchor = recommendStream.anchorRef.current + 1;
    recommendStream.begin();
    setRecommendations([]);
    try {
      await acquireApi.recommend(seed, 8, (event) => {
        recommendStream.accept(anchor, event);
        if (event.type === 'done' || event.type === 'result') {
          setRecommendations(((event.candidates as Candidate[]) ?? []).slice(0, 8));
        }
      });
    } catch (error) {
      recommendStream.fail(anchor, error);
    }
  };

  const importOne = async (candidate: Candidate) => {
    const anchor = ingestStream.anchorRef.current + 1;
    ingestStream.begin();
    try {
      await acquireApi.ingestSelected({ candidates: [candidate] }, (event) =>
        ingestStream.accept(anchor, event),
      );
      await reloadPapers();
      notify('推荐论文已导入文献库');
    } catch (error) {
      ingestStream.fail(anchor, error);
    }
  };

  const maxTopic = Math.max(1, ...topicBars.map(([, count]) => count));
  const maxYear = Math.max(1, ...yearBars.map(([, count]) => count));
  const graphEdgeCount = graph ? (graph.edgeCount ?? graph.links.length) : 0;

  return (
    <div className="page page-enter insights">
      <div className="insights__grid">
        <section className="card insights__panel" aria-labelledby="insights-topics">
          <header className="insights__panel-head">
            <h3 className="section-title" id="insights-topics">
              主题分布
            </h3>
            <span className="eyebrow">{papers.length} 篇在册</span>
          </header>
          <ul className="insights__topics">
            {topicBars.map(([topic, count]) => (
              <li key={topic}>
                <span className="insights__topic-name">{topic}</span>
                <span className="insights__bar">
                  <i style={{ width: `${(count / maxTopic) * 100}%` }} />
                </span>
                <span className="insights__topic-count">{count}</span>
              </li>
            ))}
          </ul>
        </section>

        <section className="card insights__panel" aria-labelledby="insights-years">
          <header className="insights__panel-head">
            <h3 className="section-title" id="insights-years">
              入库年份分布
            </h3>
            <span className="eyebrow">按论文年份</span>
          </header>
          <div className="insights__months">
            {yearBars.map(([year, count]) => (
              <div key={year} className="insights__month">
                <span className="insights__month-value">{count}</span>
                <span className="insights__month-bar">
                  <i style={{ height: `${(count / maxYear) * 100}%` }} />
                </span>
                <span className="insights__month-label">{year}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="card insights__panel" aria-labelledby="insights-cited">
          <header className="insights__panel-head">
            <h3 className="section-title" id="insights-cited">
              高被引馆藏
            </h3>
            <span className="eyebrow">按被引次数</span>
          </header>
          <ol className="insights__cited">
            {topCited.map((paper, index) => (
              <li key={paper.id}>
                <span className="insights__cited-rank">{String(index + 1).padStart(2, '0')}</span>
                <span className="insights__cited-copy">
                  <strong>{paper.title_zh || paper.title}</strong>
                  <small>
                    {paper.venue} {paper.year} · {paper.topic}
                  </small>
                </span>
                <span className="insights__cited-count">
                  {(paper.citations ?? 0).toLocaleString()}
                </span>
              </li>
            ))}
          </ol>
        </section>

        <section className="card insights__panel" aria-labelledby="insights-hubs">
          <header className="insights__panel-head">
            <h3 className="section-title" id="insights-hubs">
              引用图谱枢纽
            </h3>
            <button type="button" className="btn btn--sm" onClick={() => void buildGraph()} disabled={buildStream.state.running}>
              {buildStream.state.running ? '重建中…' : '重建图谱'}
            </button>
          </header>
          <StreamConsole state={buildStream.state} />
          {graph ? (
            <>
              <p className="deep__fact">
                图谱含 {graph.nodes.length} 个节点 / {graphEdgeCount} 条引用边（GET /api/citegraph）。
              </p>
              {graphEdgeCount === 0 ? (
                <p className="artifacts__empty">
                  当前未发现馆藏论文之间的引用关系。可重建图谱重新检查元数据匹配，无引用边本身不是错误。
                </p>
              ) : (
                <ol className="insights__cited">
                  {hubNodes.map((node) => (
                    <li key={node.id}>
                      <span className="insights__cited-rank">{node.indeg + node.outdeg}</span>
                      <span className="insights__cited-copy">
                        <strong>{node.title}</strong>
                        <small>
                          {node.venue} {node.year} · 入度 {node.indeg} / 出度 {node.outdeg}
                        </small>
                      </span>
                      <span className="badge badge--venue">{node.type}</span>
                    </li>
                  ))}
                </ol>
              )}
            </>
          ) : (
            <p className="artifacts__empty">图谱数据加载中或为空，可点击「重建图谱」。</p>
          )}
        </section>

        <section className="card insights__recommend" aria-labelledby="insights-rec">
          <span className="insights__recommend-mark" aria-hidden="true">
            <SparkIcon size={18} />
          </span>
          <div className="insights__rec-form">
            <h3 className="section-title" id="insights-rec">
              相似论文推荐
            </h3>
            <div className="reviews__start-row">
              <select
                className="input"
                aria-label="种子论文"
                value={seed}
                onChange={(event) => setSeed(event.target.value)}
              >
                <option value="">选择种子论文…</option>
                {papers.slice(0, 60).map((paper) => (
                  <option key={paper.id} value={paper.id}>
                    {paper.title_zh || paper.title}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => void runRecommend()}
                disabled={!seed || recommendStream.state.running}
              >
                生成推荐
              </button>
            </div>
          </div>
          <StreamConsole state={recommendStream.state} />
          <StreamConsole state={ingestStream.state} />
          {recommendations.length > 0 && (
            <ul className="deep__recs insights__rec-list">
              {recommendations.map((candidate, index) => (
                <li key={index} className="card deep__rec">
                  <div>
                    <strong>{candidate.title_zh || candidate.title}</strong>
                    <small>
                      {candidate.venue} {candidate.year} · 被引 {candidate.citations ?? 0}
                    </small>
                  </div>
                  <button type="button" className="btn btn--sm" onClick={() => void importOne(candidate)}>
                    <PlusIcon size={13} />
                    导入
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
