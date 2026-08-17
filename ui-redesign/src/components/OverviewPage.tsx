import { useEffect, useState } from 'react';

import { acquireApi, artifactApi } from '../api/client';
import type { Paper, ReviewItem, ReviewSnapshot } from '../api/types';
import type { PageId } from '../nav';
import { ArrowRightIcon, CheckIcon, SparkIcon } from './Icons';

interface OverviewPageProps {
  papers: Paper[];
  reviews: ReviewSnapshot | null;
  onNavigate: (page: PageId) => void;
  onOpenPaper: (id: string) => void;
  onRefresh: () => Promise<void>;
}

const HOUR = new Date().getHours();
const GREETING = HOUR < 6 ? '夜深了' : HOUR < 12 ? '早上好' : HOUR < 18 ? '下午好' : '晚上好';

export function OverviewPage({
  papers,
  reviews,
  onNavigate,
  onOpenPaper,
  onRefresh,
}: OverviewPageProps) {
  const [citeSummary, setCiteSummary] = useState<{ nodes: number; edges: number } | null>(null);
  const [pendingTitles, setPendingTitles] = useState<number | null>(null);

  useEffect(() => {
    acquireApi
      .citeGraph()
      .then((graph) => setCiteSummary({ nodes: graph.nodes.length, edges: graph.edges.length }))
      .catch(() => setCiteSummary(null));
    artifactApi
      .titleTranslationStatus()
      .then((status) => setPendingTitles(status.pending))
      .catch(() => setPendingTitles(null));
  }, []);

  const mastered = papers.filter((paper) => paper.status === '已理解').length;
  const reading = papers.filter((paper) => paper.status === '学习中');
  const current = reading[0];
  const dueItems: ReviewItem[] = reviews
    ? [...reviews.overdue, ...reviews.dueToday]
    : [];
  const topics = new Set(papers.map((paper) => paper.topic)).size;
  const dueCount = reviews ? reviews.counts.overdue + reviews.counts.dueToday : 0;

  const stats = [
    { label: '文献库存', value: papers.length, note: `${topics} 个研究方向` },
    {
      label: '已掌握',
      value: mastered,
      note: papers.length > 0 ? `占比 ${Math.round((mastered / papers.length) * 100)}%` : '',
    },
    { label: '今日待复习', value: dueCount, note: `已完成 ${reviews?.counts.completed ?? 0} 项` },
    { label: '学习中', value: reading.length, note: `${pendingTitles ?? 0} 篇标题待翻译` },
  ];

  return (
    <div className="page page-enter overview">
      <header className="overview__hero">
        <div>
          <h2 className="display-title">
            {GREETING}，研究者
          </h2>
          <p className="overview__hero-sub">
            {reviews
              ? `今天是 ${reviews.today}，${dueCount > 0 ? `有 ${dueCount} 项复习待处理` : '复习全部完成'}。`
              : '正在同步复习计划…'}
            {citeSummary && ` 引用图谱含 ${citeSummary.nodes} 篇 / ${citeSummary.edges} 条引用边。`}
          </p>
        </div>
        <button type="button" className="btn" onClick={() => void onRefresh()}>
          刷新数据
        </button>
      </header>

      <div className="overview__stats">
        {stats.map((stat) => (
          <div className="card overview__stat" key={stat.label}>
            <span className="eyebrow">{stat.label}</span>
            <strong>{stat.value}</strong>
            <small>{stat.note}</small>
          </div>
        ))}
      </div>

      <div className="overview__columns">
        <section className="card overview__panel" aria-labelledby="overview-due-title">
          <header className="overview__panel-head">
            <h3 className="section-title" id="overview-due-title">
              今日复习队列（逾期 + 今日到期）
            </h3>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              onClick={() => onNavigate('reviews')}
            >
              全部
              <ArrowRightIcon size={13} />
            </button>
          </header>
          <ul className="overview__due-list">
            {dueItems.slice(0, 6).map((item) => (
              <li key={item.paper_id}>
                <button
                  type="button"
                  className="overview__due-item"
                  onClick={() => onOpenPaper(item.paper_id)}
                >
                  <span
                    className={`overview__due-round${item.review_state === 'overdue' ? ' overview__due-round--late' : ''}`}
                  >
                    第 {item.current_step}/{item.total_steps} 轮
                  </span>
                  <span className="overview__due-copy">
                    <strong>{item.title_zh || item.title}</strong>
                    <small>
                      {item.venue} {item.year} · 应复习于 {item.next_due_at}
                    </small>
                  </span>
                  <ArrowRightIcon size={14} />
                </button>
              </li>
            ))}
            {dueItems.length === 0 && (
              <li className="overview__due-empty">当前没有逾期或今日到期的复习。</li>
            )}
          </ul>
        </section>

        <div className="overview__side">
          {current && (
            <section className="card overview__continue" aria-labelledby="overview-continue-title">
              <img
                className="overview__continue-art"
                src="/images/study-banner.png"
                alt="书桌与文献的极简插画"
              />
              <div className="overview__continue-body">
                <h3 className="section-title" id="overview-continue-title">
                  继续研读
                </h3>
                <p className="overview__continue-title">{current.title_zh || current.title}</p>
                <div className="overview__progress-row">
                  <div
                    className="progress"
                    role="progressbar"
                    aria-valuenow={current.relevance ?? 0}
                    aria-valuemin={0}
                    aria-valuemax={100}
                  >
                    <span style={{ width: `${Math.round((current.relevance ?? 0) * 100)}%` }} />
                  </div>
                  <small>相关度 {((current.relevance ?? 0) * 100).toFixed(0)}%</small>
                </div>
                <button
                  type="button"
                  className="btn btn--primary btn--sm"
                  onClick={() => onOpenPaper(current.id)}
                >
                  继续阅读
                </button>
              </div>
            </section>
          )}

          <section className="card overview__insight" aria-labelledby="overview-insight-title">
            <header className="overview__panel-head">
              <h3 className="section-title" id="overview-insight-title">
                快捷入口
              </h3>
              <SparkIcon size={15} />
            </header>
            <div className="overview__quick">
              <button type="button" className="btn btn--sm" onClick={() => onNavigate('acquire')}>
                <CheckIcon size={13} />
                采集新论文
              </button>
              <button type="button" className="btn btn--sm" onClick={() => onNavigate('manage')}>
                批量讲解 / 标题翻译
              </button>
              <button type="button" className="btn btn--sm" onClick={() => onNavigate('insights')}>
                重建引用图谱
              </button>
              <button type="button" className="btn btn--sm" onClick={() => onNavigate('jobs')}>
                后台任务
              </button>
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
