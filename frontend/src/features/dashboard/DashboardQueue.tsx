import type { PaperListItem } from '../../lib/api/types';
import type { SurfaceFilters } from '../../lib/workspace';

export interface DashboardQueueProps {
  readonly papers: readonly PaperListItem[];
  readonly filteredPapers: readonly PaperListItem[];
  readonly filters: SurfaceFilters;
  readonly selectedPaperId: string | null;
  readonly status: 'pending' | 'success' | 'error';
  readonly errorMessage?: string;
  readonly onFiltersChange: (patch: Partial<SurfaceFilters>) => void;
  readonly onSelect: (paperId: string) => void;
}

export function DashboardQueue({
  papers,
  filteredPapers,
  filters,
  selectedPaperId,
  status,
  errorMessage = '研究队列暂时不可用。',
  onFiltersChange,
  onSelect,
}: DashboardQueueProps) {
  return (
    <section className="dashboard-queue" aria-label="研究队列控制">
      <div className="dashboard-queue__controls">
        <label>
          <span>搜索论文</span>
          <input
            type="search"
            aria-label="筛选研究队列"
            data-panel-autofocus="true"
            value={filters.query}
            placeholder="题名、来源、类型或主题"
            onChange={(event) => onFiltersChange({ query: event.currentTarget.value })}
          />
        </label>
        <label>
          <span>论文状态</span>
          <select
            aria-label="论文状态"
            value={filters.status}
            onChange={(event) => onFiltersChange({ status: event.currentTarget.value })}
          >
            <option value="all">全部状态</option>
            <option value="未开始">未开始</option>
            <option value="学习中">学习中</option>
            <option value="已理解">已理解</option>
          </select>
        </label>
        <label>
          <span>论文排序</span>
          <select
            aria-label="论文排序"
            value={filters.sort}
            onChange={(event) => onFiltersChange({ sort: event.currentTarget.value })}
          >
            <option value="recent">最近加入</option>
            <option value="title">题名</option>
            <option value="year">年份</option>
            <option value="relevance">相关度</option>
          </select>
        </label>
      </div>

      {status === 'pending' && papers.length === 0 ? (
        <p className="dashboard-queue__state" role="status">正在载入研究队列…</p>
      ) : status === 'error' && papers.length === 0 ? (
        <p className="dashboard-queue__state" role="alert">{errorMessage}</p>
      ) : (
        <>
          <p className="dashboard-queue__count" role="status">
            显示 {filteredPapers.length} / {papers.length} 篇真实论文
          </p>
          {filteredPapers.length === 0 ? (
            <p className="dashboard-queue__state">当前筛选没有匹配论文。</p>
          ) : (
            <ol className="dashboard-queue__list" aria-label="筛选后的真实论文">
              {filteredPapers.map((paper) => (
                <li key={paper.id}>
                  <button
                    type="button"
                    aria-current={paper.id === selectedPaperId ? 'true' : undefined}
                    onClick={() => onSelect(paper.id)}
                  >
                    <strong>{paper.title}</strong>
                    {paper.titleZh ? <span lang="zh-CN">{paper.titleZh}</span> : null}
                    <small>
                      {[paper.status, paper.venue || paper.source, paper.year]
                        .filter(Boolean)
                        .join(' · ')}
                    </small>
                  </button>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </section>
  );
}
