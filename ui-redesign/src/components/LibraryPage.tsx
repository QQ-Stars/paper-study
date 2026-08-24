import { useEffect, useMemo, useState } from 'react';

import { acquireApi, v2Api } from '../api/client';
import type { Paper, StudyStatus } from '../api/types';
import { CloseIcon, NoteIcon, PdfIcon, SearchIcon, StarIcon } from './Icons';
import { PaperDetail } from './PaperDetail';
import { StreamConsole, useStream } from './StreamConsole';
import {
  filterLibraryPapers,
  paginateLibraryPapers,
  type LibrarySearchMode,
  type LibraryView,
} from './libraryQuery';

interface LibraryPageProps {
  papers: Paper[];
  loading: boolean;
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  notify: (message: string) => void;
  reloadPapers: () => Promise<void>;
  reloadReviews: () => Promise<void>;
  openPaper: (id: string) => void;
  openReader: (id: string) => void;
}

type SearchMode = LibrarySearchMode;

const STATUS_FILTERS: Array<{ id: 'all' | StudyStatus; label: string }> = [
  { id: 'all', label: '全部' },
  { id: '未开始', label: '未开始' },
  { id: '学习中', label: '学习中' },
  { id: '已理解', label: '已掌握' },
];

interface SemanticHit {
  id: string;
  title: string;
  score: number;
}

interface ChunkHit {
  paperId: string;
  excerpt: string;
  headingPath: string[];
  pageStart: number | null;
  score: number;
}

export function LibraryPage({
  papers,
  loading,
  selectedId,
  onSelect,
  notify,
  reloadPapers,
  reloadReviews,
  openPaper,
  openReader,
}: LibraryPageProps) {
  const [mode, setMode] = useState<SearchMode>('keyword');
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<'all' | StudyStatus>('all');
  const [topic, setTopic] = useState('all');
  const [libraryView, setLibraryView] = useState<LibraryView>('recent');
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [semanticHits, setSemanticHits] = useState<SemanticHit[]>([]);
  const [chunkHits, setChunkHits] = useState<ChunkHit[]>([]);
  const [chunkMode, setChunkMode] = useState<'lexical' | 'semantic' | 'hybrid'>('hybrid');
  const semStream = useStream();
  const chunkBusy = useState({ busy: false })[0];
  const favoritesOnly = libraryView === 'favorites';

  const topics = useMemo(
    () => Array.from(new Set(papers.map((paper) => paper.topic).filter(Boolean))),
    [papers],
  );

  const filtered = useMemo(
    () => filterLibraryPapers(papers, { mode, query, status, topic, view: libraryView }),
    [papers, mode, query, status, topic, libraryView],
  );

  const selected = papers.find((paper) => paper.id === selectedId) ?? null;

  /* 分页：筛选/排序/搜索变化时回到第 1 页；页数变少时 clamp 兑底 */
  useEffect(() => {
    setPage(1);
  }, [query, status, topic, libraryView, mode, pageSize, papers.length]);
  useEffect(() => {
    if (favoritesOnly && selectedId && !filtered.some((paper) => paper.id === selectedId)) {
      onSelect(null);
    }
  }, [favoritesOnly, filtered, onSelect, selectedId]);
  const pageResult = useMemo(
    () => paginateLibraryPapers(filtered, page, pageSize),
    [filtered, page, pageSize],
  );
  const { papers: paged, page: safePage, pageCount, pageStart, pageEnd } = pageResult;
  const countText = `${String(filtered.length).padStart(String(papers.length).length, '\u2007')} 篇`;

  const runSemantic = async () => {
    if (!query.trim()) return;
    const anchor = semStream.anchorRef.current + 1;
    semStream.begin();
    setSemanticHits([]);
    const hits: SemanticHit[] = [];
    try {
      await acquireApi.semsearch(query.trim(), 10, (event) => {
        semStream.accept(anchor, event);
        if (event.type === 'result' || event.type === 'done') {
          const results = (event.results as Array<Record<string, unknown>>) ?? [];
          for (const row of results) {
            const id = String(row.id ?? row.paper_id ?? '');
            const known = papers.find((paper) => paper.id === id);
            const title = String(
              row.title_zh ?? row.title ?? known?.title_zh ?? known?.title ?? id,
            );
            const score = Number(row.score ?? row.similarity ?? 0);
            if (id) hits.push({ id, title, score });
          }
          setSemanticHits(hits);
        }
      });
    } catch (error) {
      semStream.fail(anchor, error);
    }
  };

  const runChunks = async () => {
    if (!query.trim()) return;
    setChunkHits([]);
    try {
      const result = await v2Api.searchChunks({
        query: query.trim(),
        mode: chunkMode,
        limit: 12,
      });
      setChunkHits(result.items);
      if (result.items.length === 0) notify('没有命中的文档分块（需要先在「深度」页签建立索引）');
    } catch (error) {
      notify(`分块检索失败：${error instanceof Error ? error.message : error}`);
    }
  };

  return (
    <div className="page page-enter library">
      <div className="library__toolbar">
        <label className="library__search input">
          <SearchIcon size={15} />
          <input
            placeholder={
              mode === 'keyword'
                ? '检索题名 / 中文题名 / venue / 主题…'
                : mode === 'semantic'
                  ? '用自然语言描述要找的论文…'
                  : '在 PDF 分块中检索证据段落…'
            }
            aria-label="检索文献"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                if (mode === 'semantic') void runSemantic();
                if (mode === 'chunks') void runChunks();
              }
            }}
          />
          {query && (
            <button type="button" className="btn btn--ghost btn--sm" aria-label="清空" onClick={() => setQuery('')}>
              <CloseIcon size={13} />
            </button>
          )}
        </label>
        <div className="library__segment" role="tablist" aria-label="检索模式">
          {(
            [
              { id: 'keyword', label: '关键词' },
              { id: 'semantic', label: '语义检索' },
              { id: 'chunks', label: '分块检索' },
            ] as Array<{ id: SearchMode; label: string }>
          ).map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={mode === item.id}
              className={`library__segment-item${mode === item.id ? ' library__segment-item--active' : ''}`}
              onClick={() => setMode(item.id)}
            >
              {item.label}
            </button>
          ))}
        </div>
        {mode === 'keyword' && (
          <>
            <div className="library__segment" role="tablist" aria-label="状态筛选">
              {STATUS_FILTERS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  aria-selected={status === item.id}
                  className={`library__segment-item${status === item.id ? ' library__segment-item--active' : ''}`}
                  onClick={() => setStatus(item.id)}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <select
              className="input library__topic"
              aria-label="主题筛选"
              value={topic}
              onChange={(event) => setTopic(event.target.value)}
            >
              <option value="all">全部主题</option>
              {topics.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
            <select
              className="input library__topic"
              aria-label="排序与收藏筛选"
              value={libraryView}
              onChange={(event) => setLibraryView(event.target.value as LibraryView)}
            >
              <option value="recent">最近入库</option>
              <option value="favorites">已收藏</option>
              <option value="year">年份</option>
              <option value="citations">被引数</option>
              <option value="relevance">相关度</option>
            </select>
          </>
        )}
        {mode === 'chunks' && (
          <select
            className="input library__topic"
            aria-label="分块检索模式"
            value={chunkMode}
            onChange={(event) => setChunkMode(event.target.value as typeof chunkMode)}
          >
            <option value="hybrid">混合</option>
            <option value="semantic">语义</option>
            <option value="lexical">词法</option>
          </select>
        )}
        <span className="library__count">{mode === 'keyword' ? countText : ''}</span>
      </div>

      {(mode === 'semantic' || mode === 'chunks') && (
        <div className="library__mode-hint">
          {mode === 'semantic' ? (
            <>
              语义检索基于本地嵌入索引，按 Enter 或
              <button type="button" className="btn btn--sm" onClick={() => void runSemantic()} disabled={semStream.state.running}>
                执行检索
              </button>
            </>
          ) : (
            <>
              分块检索需要论文已建立 v2 文档索引，
              <button type="button" className="btn btn--sm" onClick={() => void runChunks()} disabled={chunkBusy.busy}>
                执行检索
              </button>
            </>
          )}
        </div>
      )}
      {mode === 'semantic' && <StreamConsole state={semStream.state} />}
      {mode === 'semantic' && semanticHits.length > 0 && (
        <ul className="library__semhits">
          {semanticHits.map((hit) => (
            <li key={hit.id}>
              <button type="button" className="library__semhit" onClick={() => openPaper(hit.id)}>
                <span className="library__semhit-score">{(hit.score * 100).toFixed(0)}%</span>
                <span>{hit.title}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      {mode === 'chunks' && chunkHits.length > 0 && (
        <ul className="library__chunks">
          {chunkHits.map((hit, index) => (
            <li key={index} className="card library__chunk">
              <header>
                <span className="badge badge--seal">得分 {hit.score.toFixed(2)}</span>
                <button type="button" className="btn btn--ghost btn--sm" onClick={() => openPaper(hit.paperId)}>
                  打开论文
                </button>
              </header>
              <small>
                {hit.headingPath.join(' › ') || '正文'}
                {hit.pageStart !== null && ` · 第 ${hit.pageStart} 页起`}
              </small>
              <p>{hit.excerpt}</p>
            </li>
          ))}
        </ul>
      )}

      {mode === 'keyword' && (
        <div className={`library__grid${selected ? ' library__grid--detail' : ''}`}>
          <div className="library__listcol">
          <ul className="library__list">
            {loading && <li className="library__empty">正在从后端加载文献库…</li>}
            {!loading &&
              paged.map((paper) => (
                <li key={paper.id}>
                  <button
                    type="button"
                    className={`library__row${paper.id === selectedId ? ' library__row--active' : ''}`}
                    onClick={() => onSelect(paper.id === selectedId ? null : paper.id)}
                  >
                    <span className="library__row-glyphs">
                      <span className={`status-dot status-dot--${paper.status}`} aria-hidden="true" />
                      {paper.favorite === 1 && <StarIcon size={13} className="glyph-star" />}
                      {paper.hasPdf && <PdfIcon size={13} className="glyph-pdf" />}
                      {paper.hasNote === 1 && <NoteIcon size={13} className="glyph-note" />}
                    </span>
                    <span className="library__row-copy">
                      <strong>{paper.title_zh || paper.title}</strong>
                      <small>{paper.title}</small>
                      <span className="library__row-meta">
                        <span className="badge badge--venue">
                          {paper.venue ?? ''} {paper.year ?? ''}
                        </span>
                        {paper.ccf && <span className="badge badge--seal">CCF-{paper.ccf}</span>}
                        {paper.topic && <span className="library__topic-tag">{paper.topic}</span>}
                        {paper.type && <span className="library__topic-tag">{paper.type}</span>}
                        <span className="library__status-text">被引 {paper.citations ?? 0}</span>
                        <span className="library__status-text">{paper.source}</span>
                        <span className="library__status-text">{paper.status}</span>
                      </span>
                    </span>
                    <span className="library__row-aside">
                      <span className="library__row-rel">
                        {((paper.relevance ?? 0) * 100).toFixed(0)}%
                      </span>
                      <small>{(paper.created_at ?? '').slice(0, 10)}</small>
                    </span>
                  </button>
                </li>
              ))}
            {!loading && filtered.length === 0 && (
              <li className="library__empty">
                {favoritesOnly
                  ? query.trim() || status !== 'all' || topic !== 'all'
                    ? '没有符合当前条件的已收藏文献，调整关键词或筛选条件试试。'
                    : '暂无已收藏的文献'
                  : '没有符合筛选条件的文献，换个关键词试试。'}
              </li>
            )}
          </ul>

          {!loading && filtered.length > 0 && (
            <div className="library__pager" aria-label="分页">
              <span className="library__pager-info">
                第 {pageStart}–{pageEnd} 条 · 共 {filtered.length} 篇
              </span>
              <div className="library__pager-controls">
                <button
                  type="button"
                  className="btn btn--sm"
                  disabled={safePage <= 1}
                  onClick={() => setPage(safePage - 1)}
                >
                  上一页
                </button>
                <span className="library__pager-page">
                  {safePage} / {pageCount}
                </span>
                <button
                  type="button"
                  className="btn btn--sm"
                  disabled={safePage >= pageCount}
                  onClick={() => setPage(safePage + 1)}
                >
                  下一页
                </button>
                <select
                  className="input library__pager-size"
                  aria-label="每页条数"
                  value={pageSize}
                  onChange={(event) => setPageSize(Number(event.target.value))}
                >
                  {[20, 30, 50].map((size) => (
                    <option key={size} value={size}>
                      {size} 条/页
                    </option>
                  ))}
                </select>
              </div>
            </div>
          )}
          </div>

          {selected && (
            <PaperDetail
              key={selected.id}
              paper={selected}
              notify={notify}
              reloadPapers={reloadPapers}
              reloadReviews={reloadReviews}
              openReader={() => openReader(selected.id)}
              onDeleted={() => onSelect(null)}
            />
          )}
        </div>
      )}
    </div>
  );
}
