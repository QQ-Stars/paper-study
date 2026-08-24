import { useMemo, useState } from 'react';

import { acquireApi, jobApi } from '../api/client';
import type { Candidate, Paper, StreamEvent } from '../api/types';
import { CheckIcon, CompassIcon, DocumentIcon, PlusIcon, SearchIcon, SparkIcon } from './Icons';
import { StreamConsole, useStream } from './StreamConsole';

interface AcquirePageProps {
  papers: Paper[];
  notify: (message: string) => void;
  reloadPapers: () => Promise<void>;
}

type Phase = 'idle' | 'searched';

const SOURCES = [
  { id: 'arxiv', label: 'arXiv' },
  { id: 'semanticscholar', label: 'Semantic Scholar' },
  { id: 'dblp', label: 'DBLP' },
];

/* 题名归一化：去空白与标点、小写，用于跨源去重匹配 */
function normalizeTitle(value: string): string {
  return value.toLowerCase().replace(/[\s\p{P}]+/gu, '');
}

export function AcquirePage({ papers, notify, reloadPapers }: AcquirePageProps) {
  const [query, setQuery] = useState('');
  const [sources, setSources] = useState<string[]>(['arxiv', 'semanticscholar']);
  const [years, setYears] = useState('2024-2026');
  const [max, setMax] = useState(10);
  const [minRelevance, setMinRelevance] = useState(0);
  const [expand, setExpand] = useState(false);
  const [onlyA, setOnlyA] = useState(false);
  const [deep, setDeep] = useState(false);
  const [downloadPdf, setDownloadPdf] = useState(true);
  const [selectedQueries, setSelectedQueries] = useState<ReadonlySet<string>>(new Set());
  const [expandWords, setExpandWords] = useState<string[]>([]);
  const [phase, setPhase] = useState<Phase>('idle');
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [checked, setChecked] = useState<ReadonlySet<number>>(new Set());
  const searchStream = useStream();
  const ingestStream = useStream();

  /* 去重：后端 /api/search 候选无去重字段，前端对照 GET /api/papers 全量匹配
   * （优先 arxiv_id/doi/s2_id，其次归一化题名），与旧前端 inLibrary 行为对齐。 */
  const libraryKeys = useMemo(() => {
    const keys = new Set<string>();
    for (const paper of papers) {
      if (paper.arxiv_id) keys.add(`arxiv:${paper.arxiv_id.toLowerCase()}`);
      if (paper.doi) keys.add(`doi:${paper.doi.toLowerCase()}`);
      if (paper.s2_id) keys.add(`s2:${paper.s2_id.toLowerCase()}`);
      if (paper.title) keys.add(`t:${normalizeTitle(paper.title)}`);
    }
    return keys;
  }, [papers]);

  const isExisting = (candidate: Candidate): boolean => {
    const arxivId = String(candidate.arxiv_id ?? '').toLowerCase();
    const doi = String(candidate.doi ?? '').toLowerCase();
    const s2Id = String(candidate.s2_id ?? '').toLowerCase();
    return (
      (arxivId !== '' && libraryKeys.has(`arxiv:${arxivId}`)) ||
      (doi !== '' && libraryKeys.has(`doi:${doi}`)) ||
      (s2Id !== '' && libraryKeys.has(`s2:${s2Id}`)) ||
      libraryKeys.has(`t:${normalizeTitle(String(candidate.title ?? ''))}`)
    );
  };

  const toggleSource = (id: string) => {
    setSources((prev) => (prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]));
  };

  const toggleWord = (word: string) => {
    setSelectedQueries((prev) => {
      const next = new Set(prev);
      if (next.has(word)) next.delete(word);
      else next.add(word);
      return next;
    });
  };

  const runExpand = async () => {
    if (!query.trim()) return;
    try {
      const result = await acquireApi.expand(query.trim(), 6);
      if (result.ok === false) {
        setExpandWords([]);
        setSelectedQueries(new Set());
        notify(`扩展失败：${String(result.error ?? '未生成有效扩展检索词')}`);
        return;
      }
      const words = Array.isArray(result.queries)
        ? result.queries.filter((word): word is string => typeof word === 'string' && word.trim() !== '')
        : [];
      setExpandWords(words);
      setSelectedQueries(new Set(words));
      if (words.length === 0) {
        notify('未返回扩展检索词');
      } else if (result.fallback) {
        notify(`模型暂不可用，已使用本地生成 ${words.length} 个英文扩展检索词（已默认全选，可点击取舍）`);
      } else {
        notify(`已生成 ${words.length} 个扩展检索词（已默认全选，可点击取舍）`);
      }
    } catch (error) {
      notify(`扩展失败：${error instanceof Error ? error.message : error}`);
    }
  };

  const runSearch = async (useQuery?: string) => {
    const finalQuery = (useQuery ?? query).trim();
    if (!finalQuery || sources.length === 0) {
      notify('请输入研究方向并至少选择一个数据源');
      return;
    }
    const anchor = searchStream.anchorRef.current + 1;
    searchStream.begin();
    setCandidates([]);
    setChecked(new Set());
    try {
      await acquireApi.search(
        {
          query: finalQuery,
          sources,
          years,
          max,
          minRelevance: minRelevance > 0 ? minRelevance : undefined,
          expand,
          onlyA,
          queries: selectedQueries.size > 0 ? [...selectedQueries] : undefined,
        },
        (event: StreamEvent) => {
          searchStream.accept(anchor, event);
          if ((event.type === 'done' || event.type === 'result') && event.ok !== false) {
            const list = ((event.candidates as Candidate[]) ?? []).filter(
              (item) => item && typeof item.title === 'string',
            );
            setCandidates(list);
            notify(`检索完成，命中 ${list.length} 篇候选`);
          }
        },
      );
      setPhase('searched');
    } catch (error) {
      searchStream.fail(anchor, error);
    }
  };

  const toggleChecked = (index: number) => {
    setChecked((prev) => {
      const next = new Set(prev);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  };

  const importSelected = async () => {
    const picked = candidates.filter((_, index) => checked.has(index));
    if (picked.length === 0) return;
    const anchor = ingestStream.anchorRef.current + 1;
    ingestStream.begin();
    try {
      await acquireApi.ingestSelected(
        { candidates: picked, deep, downloadPdf },
        (event) => ingestStream.accept(anchor, event),
      );
      await reloadPapers();
      notify(`已导入 ${picked.length} 篇到文献库`);
    } catch (error) {
      ingestStream.fail(anchor, error);
    }
  };

  const createBackgroundJob = async () => {
    if (!query.trim() || sources.length === 0) {
      notify('请输入研究方向并选择数据源');
      return;
    }
    const result = await jobApi.create({
      query: query.trim(),
      sources,
      years,
      max,
      queries: selectedQueries.size > 0 ? [...selectedQueries] : undefined,
    });
    notify(
      result.ok
        ? `后台采集任务已创建（#${result.id}），可在「任务」页跟踪与确认候选`
        : `失败：${result.error}`,
    );
  };

  return (
    <div className="page page-enter acquire">
      <div className="acquire__hero">
        <span className="acquire__hero-mark" aria-hidden="true">
          <CompassIcon size={22} />
        </span>
        <h2 className="display-title">从世界到你的书桌</h2>
        <p>多源检索 → 查看候选 → 勾选导入；全部处理在本机完成。</p>
        <label className="acquire__search">
          <SearchIcon size={16} />
          <input
            className="acquire__search-input"
            placeholder="输入研究方向，例如「检索增强生成」…"
            aria-label="检索新论文"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void runSearch();
            }}
          />
        </label>
        <div className="acquire__options">
          {SOURCES.map((source) => (
            <label key={source.id} className="acquire__check">
              <input
                type="checkbox"
                checked={sources.includes(source.id)}
                onChange={() => toggleSource(source.id)}
              />
              {source.label}
            </label>
          ))}
          <input
            className="input acquire__years"
            aria-label="年份范围"
            value={years}
            onChange={(event) => setYears(event.target.value)}
          />
          <select
            className="input acquire__max"
            aria-label="候选上限"
            value={max}
            onChange={(event) => setMax(Number(event.target.value))}
          >
            {[5, 10, 20, 40, 60].map((value) => (
              <option key={value} value={value}>
                上限 {value}
              </option>
            ))}
          </select>
          <button type="button" className="btn btn--sm" onClick={() => void runExpand()}>
            <SparkIcon size={13} />
            扩展检索词
          </button>
        </div>
        <div className="acquire__options">
          <label className="acquire__check">
            <input
              type="checkbox"
              checked={expand}
              onChange={(event) => setExpand(event.target.checked)}
            />
            自动扩展查询
          </label>
          <label className="acquire__check">
            <input
              type="checkbox"
              checked={onlyA}
              onChange={(event) => setOnlyA(event.target.checked)}
            />
            仅 CCF-A
          </label>
          <label className="acquire__check acquire__check--inline">
            最低相关度
            <input
              className="input acquire__relevance"
              type="number"
              min={0}
              max={1}
              step={0.05}
              aria-label="最低相关度"
              value={minRelevance}
              onChange={(event) => setMinRelevance(Number(event.target.value) || 0)}
            />
          </label>
          <label className="acquire__check">
            <input
              type="checkbox"
              checked={deep}
              onChange={(event) => setDeep(event.target.checked)}
            />
            导入时深度补全（deep）
          </label>
          <label className="acquire__check">
            <input
              type="checkbox"
              checked={downloadPdf}
              onChange={(event) => setDownloadPdf(event.target.checked)}
            />
            导入时下载 PDF
          </label>
        </div>
        {expandWords.length > 0 && (
          <div className="acquire__chips" aria-label="自定义检索词">
            <span className="acquire__chips-label">
              自定义检索词（勾选后随检索下发，不勾选则只用主方向）：
            </span>
            <button type="button" className="acquire__chip acquire__chip--root" onClick={() => void runSearch()}>
              {query} · 直接检索
            </button>
            {expandWords.map((word) => (
              <button
                key={word}
                type="button"
                className={`acquire__chip${selectedQueries.has(word) ? ' acquire__chip--on' : ''}`}
                aria-pressed={selectedQueries.has(word)}
                onClick={() => toggleWord(word)}
              >
                {selectedQueries.has(word) ? '✓ ' : ''}
                {word}
              </button>
            ))}
          </div>
        )}
      </div>

      <StreamConsole state={searchStream.state} />

      {phase === 'idle' && (
        <div className="acquire__idle">
          <div className="card acquire__hint">
            <h3 className="section-title">本机研究主题</h3>
            <p className="artifacts__empty">
              在「设置」中配置的 researchTheme 会作为默认研究方向提示。
            </p>
            <button
              type="button"
              className="btn"
              onClick={() => {
                setQuery('LLM hallucination detection and mitigation');
                void runSearch('LLM hallucination detection and mitigation');
              }}
            >
              以当前主题检索一次
            </button>
          </div>
          <button type="button" className="card acquire__local" onClick={() => notify('请到「管理」页使用本地 PDF 扫描与导入')}>
            <DocumentIcon size={20} />
            <span className="acquire__local-copy">
              <strong>导入本地 PDF</strong>
              <small>扫描文件夹并批量入库（在「管理」页）</small>
            </span>
            <span className="acquire__local-cta">前往</span>
          </button>
        </div>
      )}

      {candidates.length > 0 && (
        <section className="acquire__results" aria-label="候选结果">
          <header className="acquire__results-head">
            <h3 className="section-title">
              命中 {candidates.length} 篇 · 已选 {checked.size} 篇
            </h3>
            <div className="deep__actions">
              <button
                type="button"
                className="btn btn--sm"
                onClick={() =>
                  setChecked(
                    new Set(
                      candidates
                        .map((candidate, index) => ({ candidate, index }))
                        .filter(({ candidate }) => !isExisting(candidate))
                        .map(({ index }) => index),
                    ),
                  )
                }
              >
                全选未入库
              </button>
              <button
                type="button"
                className="btn btn--primary btn--sm"
                disabled={checked.size === 0 || ingestStream.state.running}
                onClick={() => void importSelected()}
              >
                <PlusIcon size={13} />
                导入所选
              </button>
              <button type="button" className="btn btn--sm" onClick={() => void createBackgroundJob()}>
                存为后台任务
              </button>
            </div>
          </header>
          <StreamConsole state={ingestStream.state} />
          <ul className="acquire__list">
            {candidates.map((candidate, index) => {
              const existing = isExisting(candidate);
              const picked = checked.has(index);
              return (
                <li
                  key={index}
                  className={`card acquire__card${picked ? ' acquire__card--checked' : ''}${existing ? ' acquire__card--existing' : ''}`}
                >
                  <label className="acquire__card-check">
                    <input
                      type="checkbox"
                      disabled={existing}
                      checked={picked}
                      onChange={() => toggleChecked(index)}
                      aria-label={`选择 ${candidate.title_zh || candidate.title}`}
                    />
                  </label>
                  <div className="acquire__card-copy">
                    <span className="acquire__card-meta">
                      {candidate.venue && (
                        <span className="badge badge--venue">
                          {candidate.venue} {candidate.year}
                        </span>
                      )}
                      {candidate.topic && (
                        <span className="library__topic-tag">{candidate.topic}</span>
                      )}
                      {candidate.arxiv_id && (
                        <span className="acquire__card-source">arXiv {String(candidate.arxiv_id)}</span>
                      )}
                    </span>
                    <strong>{candidate.title_zh || candidate.title}</strong>
                    {candidate.title_zh && (
                      <small className="acquire__card-origin">{candidate.title}</small>
                    )}
                    {candidate.tldr && (
                      <small className="acquire__card-tldr">{candidate.tldr}</small>
                    )}
                  </div>
                  <div className="acquire__card-aside">
                    {candidate.relevance !== undefined && (
                      <span className="acquire__card-rel">
                        {((Number(candidate.relevance) || 0) * 100).toFixed(0)}%
                      </span>
                    )}
                    <small>被引 {candidate.citations ?? 0}</small>
                    {existing ? (
                      <span className="badge badge--venue">已入库</span>
                    ) : picked ? (
                      <span className="badge badge--jade">
                        <CheckIcon size={12} />
                        待导入
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="btn btn--sm"
                        onClick={() => toggleChecked(index)}
                      >
                        <PlusIcon size={13} />
                        导入
                      </button>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
