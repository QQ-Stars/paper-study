import { useState } from 'react';

import { libraryApi, reviewApi } from '../api/client';
import type { Paper, ReviewItem, ReviewSnapshot } from '../api/types';
import { CheckIcon } from './Icons';

interface ReviewsPageProps {
  reviews: ReviewSnapshot | null;
  notify: (message: string) => void;
  reloadPapers: () => Promise<void>;
  reloadReviews: () => Promise<void>;
  openPaper: (id: string) => void;
}

const GROUPS: Array<{ key: 'overdue' | 'dueToday' | 'upcoming' | 'completed'; label: string; note: string }> = [
  { key: 'overdue', label: '已逾期', note: '应复习日早于今天，优先处理' },
  { key: 'dueToday', label: '今日到期', note: '按间隔轮次今天应复习' },
  { key: 'upcoming', label: '即将到期', note: '间隔未满，等待排期' },
  { key: 'completed', label: '已完成计划', note: '七轮全部完成' },
];

export function ReviewsPage({
  reviews,
  notify,
  reloadPapers,
  reloadReviews,
  openPaper,
}: ReviewsPageProps) {
  const [busy, setBusy] = useState('');

  if (!reviews) {
    return (
      <div className="page page-enter reviews">
        <div className="card reviews__placeholder">正在加载复习快照…（GET /api/reviews）</div>
      </div>
    );
  }

  const total = reviews.counts.overdue + reviews.counts.dueToday;

  const complete = async (item: ReviewItem) => {
    setBusy(item.paper_id);
    const result = await reviewApi.complete(item.paper_id);
    setBusy('');
    if (result.ok) {
      const nextDue = (result.plan as { next_due_at?: string } | undefined)?.next_due_at;
      notify(
        item.current_step >= item.total_steps
          ? '七轮复习全部完成 🎉'
          : `已完成第 ${item.current_step} 轮复习${nextDue ? `，下一轮 ${nextDue}` : ''}`,
      );
      await Promise.all([reloadReviews(), reloadPapers()]);
    } else {
      notify(`失败：${result.error ?? '未知错误'}`);
    }
  };

  const startUnplanned = async (paper: Paper) => {
    const result = await reviewApi.start(paper.id);
    notify(result.ok ? `已为「${paper.title_zh || paper.title}」创建七轮计划` : `失败：${result.error}`);
    await reloadReviews();
  };

  return (
    <div className="page page-enter reviews">
      <header className="reviews__head card">
        <div className="reviews__counts">
          {(
            [
              ['overdue', '逾期'],
              ['dueToday', '今日'],
              ['upcoming', '待期'],
              ['completed', '已完成'],
            ] as Array<[keyof ReviewSnapshot['counts'], string]>
          ).map(([key, label]) => (
            <div className="reviews__count" key={key}>
              <strong>{reviews.counts[key]}</strong>
              <span>{label}</span>
            </div>
          ))}
        </div>
        <div className="reviews__head-copy">
          <h2 className="display-title">艾宾浩斯复习</h2>
          <p>
            记忆沿七轮间隔巩固：20 分钟 → 1 小时 → 8 小时 → 1 天 → 2 天 → 6 天 → 15 天。
            当前共 {total} 项待处理，完成一项自动排入下一轮。
          </p>
          <button type="button" className="btn btn--sm" onClick={() => void reloadReviews()}>
            刷新快照
          </button>
        </div>
      </header>

      <div className="reviews__rounds">
        {GROUPS.map((group) => {
          const items = reviews[group.key] ?? [];
          if (items.length === 0) return null;
          return (
            <section key={group.key} className="reviews__round" aria-label={group.label}>
              <header className="reviews__round-head">
                <h3>{group.label}</h3>
                <span className="reviews__round-interval">{items.length} 项 · {group.note}</span>
              </header>
              <ul className="reviews__cards">
                {items.map((item) => {
                  const done = group.key === 'completed';
                  const pct = Math.round(
                    ((done ? item.total_steps : item.completed_steps) / item.total_steps) * 100,
                  );
                  return (
                    <li key={`${group.key}-${item.paper_id}`}>
                      <div
                        className={`card reviews__card${done ? ' reviews__card--done' : ''}${
                          group.key === 'overdue' ? ' reviews__card--late' : ''
                        }`}
                      >
                        <span className="reviews__card-topic">{item.venue}</span>
                        <strong>{item.title_zh || item.title}</strong>
                        <small>
                          {item.venue} {item.year} · 第 {done ? item.total_steps : item.current_step}/
                          {item.total_steps} 轮 · 应复习 {item.next_due_at}
                        </small>
                        <div className="reviews__card-progress">
                          <div className="progress">
                            <span style={{ width: `${pct}%` }} />
                          </div>
                          <small>{pct}%</small>
                        </div>
                        <div className="reviews__card-actions">
                          <button
                            type="button"
                            className={done ? 'btn btn--sm' : 'btn btn--primary btn--sm'}
                            disabled={done || busy === item.paper_id}
                            onClick={() => void complete(item)}
                          >
                            {done ? (
                              <>
                                <CheckIcon size={13} />
                                七轮完成
                              </>
                            ) : (
                              '标记本轮完成'
                            )}
                          </button>
                          <button
                            type="button"
                            className="btn btn--ghost btn--sm"
                            onClick={() => openPaper(item.paper_id)}
                          >
                            详情
                          </button>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}

        <StartPanel onStart={startUnplanned} />
      </div>
    </div>
  );
}

/* 为未入计划的论文创建七轮复习计划（POST /api/reviews/start） */

function StartPanel({ onStart }: { onStart: (paper: Paper) => Promise<void> }) {
  const [query, setQuery] = useState('');
  const [matches, setMatches] = useState<Paper[]>([]);

  return (
    <section className="card reviews__start-panel" aria-label="开始新计划">
      <h3 className="section-title">为论文开始七轮计划</h3>
      <p className="artifacts__empty">输入论文题名或 ID 检索（数据来自 GET /api/papers），点击即可创建。</p>
      <div className="reviews__start-row">
        <input
          className="input"
          placeholder="题名 / 中文题名 / ID 片段…"
          value={query}
          aria-label="检索论文"
          onChange={async (event) => {
            const value = event.target.value;
            setQuery(value);
            if (!value.trim()) {
              setMatches([]);
              return;
            }
            const all = await libraryApi.listPapers();
            const q = value.trim().toLowerCase();
            setMatches(
              all
                .filter(
                  (paper) =>
                    paper.title.toLowerCase().includes(q) ||
                    paper.title_zh.includes(value.trim()) ||
                    paper.id.toLowerCase().includes(q),
                )
                .slice(0, 5),
            );
          }}
        />
      </div>
      {matches.length > 0 && (
        <ul className="reviews__start-list">
          {matches.map((paper) => (
            <li key={paper.id}>
              <button type="button" onClick={() => void onStart(paper)}>
                {paper.title_zh || paper.title}
                <small>
                  {paper.venue} {paper.year} · {paper.status}
                </small>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
