import { useEffect, useMemo, useState } from 'react';

import { artifactApi, libraryApi } from '../api/client';
import type { Paper, StudyStatus } from '../api/types';
import { ArrowRightIcon, BookmarkIcon, StarIcon } from './Icons';
import { MarkdownView } from './MarkdownView';
import { PdfViewer } from './PdfViewer';
import { SelectionTranslate } from './SelectionTranslate';

interface ReaderPageProps {
  papers: Paper[];
  paperId: string | null;
  onSwitch: (id: string) => void;
  onBack: () => void;
  notify: (message: string) => void;
  reloadPapers: () => Promise<void>;
  readingQueueIds: string[];
  updateReadingQueue: (id: string, queued: boolean) => void;
}

type ReaderTab = 'overview' | 'explainer' | 'translation' | 'ocr' | 'note' | 'pdf';

const TABS: Array<{ id: ReaderTab; label: string }> = [
  { id: 'overview', label: '摘要与贡献' },
  { id: 'explainer', label: 'AI 讲解' },
  { id: 'translation', label: '全文翻译' },
  { id: 'ocr', label: 'OCR 全文' },
  { id: 'note', label: '研究笔记' },
  { id: 'pdf', label: 'PDF 阅读' },
];

const STATUS_CYCLE: StudyStatus[] = ['未开始', '学习中', '已理解'];

type CiteBrief = {
  id: string;
  title: string;
  titleZh: string;
  year: string;
  venue: string;
  tldr: string;
};

type TranslateHistoryEntry = {
  t: string;
  src: string;
  dst: string;
};

const TRANSLATE_HISTORY_PREFIX = 'paper-study:translate-history:';

function readTranslateHistory(paperId: string): TranslateHistoryEntry[] {
  try {
    const raw = localStorage.getItem(TRANSLATE_HISTORY_PREFIX + paperId);
    const parsed = raw ? (JSON.parse(raw) as unknown) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(
        (item): item is TranslateHistoryEntry =>
          !!item &&
          typeof item === 'object' &&
          typeof (item as TranslateHistoryEntry).t === 'string' &&
          typeof (item as TranslateHistoryEntry).src === 'string' &&
          typeof (item as TranslateHistoryEntry).dst === 'string',
      )
      .slice(-50);
  } catch {
    return [];
  }
}

function translateHistoryTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}

/* 按标题行（# ～ ######）把 Markdown 切块，用于 OCR×翻译双语对照的尽力对齐 */
function splitSections(md: string): Array<{ heading: string; body: string }> {
  const sections: Array<{ heading: string; body: string }> = [];
  let heading = '';
  let buffer: string[] = [];
  const flush = () => {
    if (heading || buffer.some((line) => line.trim())) {
      sections.push({ heading, body: buffer.join('\n') });
    }
    heading = '';
    buffer = [];
  };
  for (const line of md.split(/\r?\n/)) {
    if (/^#{1,6}\s/.test(line)) {
      flush();
      heading = line;
    } else {
      buffer.push(line);
    }
  }
  flush();
  return sections;
}

export function ReaderPage({
  papers,
  paperId,
  onSwitch,
  onBack,
  notify,
  reloadPapers,
  readingQueueIds,
  updateReadingQueue,
}: ReaderPageProps) {
  const [tab, setTab] = useState<ReaderTab>('overview');
  const [fStatus, setFStatus] = useState<'all' | StudyStatus>('all');
  const [fTopic, setFTopic] = useState('all');
  const [fSource, setFSource] = useState('all');
  const [fYear, setFYear] = useState('all');
  const [fFav, setFFav] = useState<'all' | 'fav'>('all');
  const [fQueue, setFQueue] = useState<'all' | 'queue'>('all');
  const [content, setContent] = useState({ loading: false, text: '', error: '' });
  const [regen, setRegen] = useState<{
    kind: 'explainer' | 'translation' | null;
    log: string;
  }>({ kind: null, log: '' });
  const [pdfInfo, setPdfInfo] = useState<{ hasPdf: boolean; canDownload: boolean } | null>(null);
  const [pdfUrl, setPdfUrl] = useState('');
  const [ocr, setOcr] = useState<{
    phase: 'idle' | 'loading' | 'done' | 'error';
    progress: string;
    markdown: string;
    error: string;
    saved: boolean;
    savedChecked: boolean;
  }>({ phase: 'idle', progress: '', markdown: '', error: '', saved: false, savedChecked: false });
  const [citeCtx, setCiteCtx] = useState<{ cites: CiteBrief[]; citedBy: CiteBrief[] }>({
    cites: [],
    citedBy: [],
  });
  /* 翻译 tab 的 OCR×中文双语对照：按需拉取已落库 OCR Markdown，按标题切块尽力配对 */
  const [pair, setPair] = useState<{ on: boolean; loading: boolean; ocrText: string; checked: boolean }>({
    on: false,
    loading: false,
    ocrText: '',
    checked: false,
  });
  const [authors, setAuthors] = useState<string[]>([]);
  const [translateHistory, setTranslateHistory] = useState<TranslateHistoryEntry[]>([]);
  const [historyOpen, setHistoryOpen] = useState(false);

  const paper = papers.find((item) => item.id === paperId) ?? null;

  /* 页内可切换范围：按属性筛选后的论文序列 */
  const scope = useMemo(
    () =>
      papers.filter(
        (item) =>
          (fStatus === 'all' || item.status === fStatus) &&
          (fTopic === 'all' || item.topic === fTopic) &&
          (fSource === 'all' || item.source === fSource) &&
          (fYear === 'all' || item.year === fYear) &&
          (fFav === 'all' || item.favorite === 1) &&
          (fQueue === 'all' || readingQueueIds.includes(item.id)),
      ),
    [papers, fStatus, fTopic, fSource, fYear, fFav, fQueue, readingQueueIds],
  );
  const scopeIndex = paper ? scope.findIndex((item) => item.id === paper.id) : -1;
  const prevPaper = scopeIndex > 0 ? scope[scopeIndex - 1] : null;
  const nextPaper = scopeIndex >= 0 && scopeIndex < scope.length - 1 ? scope[scopeIndex + 1] : null;

  /* 筛选变化后当前论文被滤出范围时，自动跳到范围内首篇，避免正文与选择器脱节 */
  useEffect(() => {
    if (paper && scopeIndex === -1 && scope.length > 0) {
      onSwitch(scope[0].id);
    }
  }, [paper, scope, scopeIndex, onSwitch]);

  const topics = useMemo(
    () => Array.from(new Set(papers.map((item) => item.topic).filter(Boolean))),
    [papers],
  );
  const sources = useMemo(
    () => Array.from(new Set(papers.map((item) => item.source).filter(Boolean))),
    [papers],
  );
  const years = useMemo(
    () =>
      Array.from(new Set(papers.map((item) => item.year).filter(Boolean))).sort(
        (a, b) => Number(b) - Number(a),
      ),
    [papers],
  );

  /* 讲解 / 翻译 / 笔记按需加载；PDF 状态随论文变化刷新 */
  useEffect(() => {
    setTab('overview');
    setContent({ loading: false, text: '', error: '' });
    setPdfUrl('');
    setPdfInfo(null);
    setOcr({ phase: 'idle', progress: '', markdown: '', error: '', saved: false, savedChecked: false });
    setCiteCtx({ cites: [], citedBy: [] });
    setPair({ on: false, loading: false, ocrText: '', checked: false });
    setAuthors([]);
    setHistoryOpen(false);
    if (!paper) return;
    let cancelled = false;
    setTranslateHistory(readTranslateHistory(paper.id));
    libraryApi
      .pdfStatus(paper.id)
      .then((status) => {
        if (!cancelled) setPdfInfo({ hasPdf: status.hasPdf, canDownload: status.canDownload });
      })
      .catch(() => {
        if (!cancelled) setPdfInfo(null);
      });
    libraryApi
      .citeContext(paper.id)
      .then((ctx) => {
        if (!cancelled && ctx.ok) setCiteCtx({ cites: ctx.cites ?? [], citedBy: ctx.citedBy ?? [] });
      })
      .catch(() => {
        if (!cancelled) setCiteCtx({ cites: [], citedBy: [] });
      });
    libraryApi
      .paperAuthors(paper.id)
      .then((result) => {
        if (!cancelled) setAuthors(result.ok && Array.isArray(result.authors) ? result.authors : []);
      })
      .catch(() => {
        if (!cancelled) setAuthors([]);
      });
    return () => {
      cancelled = true;
    };
  }, [paper?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!paper) return;
    if (tab === 'explainer' || tab === 'translation' || tab === 'note') {
      setContent({ loading: true, text: '', error: '' });
      const loader =
        tab === 'explainer'
          ? artifactApi.getExplainer(paper.id)
          : tab === 'translation'
            ? artifactApi.getTranslation(paper.id)
            : artifactApi.getNote(paper.id);
      loader
        .then((text) => setContent({ loading: false, text, error: '' }))
        .catch((error: unknown) =>
          setContent({
            loading: false,
            text: '',
            error: error instanceof Error ? error.message : String(error),
          }),
        );
    }
  }, [tab, paper?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  /* 重新生成讲解/翻译：POST /api/explain | /api/translate（NDJSON 流）；
   * 启用 OCR 提取时后端自动优先使用 OCR Markdown 作为全文源。 */
  const regenerate = async (kind: 'explainer' | 'translation') => {
    if (!paper) return;
    setRegen({ kind, log: '正在启动…' });
    try {
      const streamFn =
        kind === 'explainer' ? artifactApi.explain : artifactApi.translate;
      await streamFn(paper.id, (event) => {
        let line = String(event.line ?? event.message ?? '');
        if (line.startsWith('STAGE::')) line = line.split('::').slice(2).join('::');
        if (line) setRegen((prev) => ({ ...prev, log: line }));
        if (event.type === 'result' && event.ok === false) {
          throw new Error(String(event.error ?? '生成失败'));
        }
      });
      const text =
        kind === 'explainer'
          ? await artifactApi.getExplainer(paper.id)
          : await artifactApi.getTranslation(paper.id);
      setContent({ loading: false, text, error: '' });
      setRegen({ kind: null, log: '' });
      notify(
        kind === 'explainer'
          ? '讲解已重新生成（启用 OCR 提取时优先使用 OCR Markdown）'
          : '全文翻译已重新生成（启用 OCR 提取时优先使用 OCR Markdown）',
      );
    } catch (error) {
      setRegen({ kind: null, log: '' });
      notify(
        `重新生成失败：${error instanceof Error ? error.message : String(error)}`,
      );
    }
  };

  /* PDF tab：取字节转 application/pdf Blob 供内嵌阅读器渲染 */
  useEffect(() => {
    if (tab !== 'pdf' || !paper || !pdfInfo?.hasPdf) return;
    let cancelled = false;
    let objectUrl = '';
    fetch(libraryApi.pdfUrl(paper.id))
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.blob();
      })
      .then((blob) => {
        if (cancelled || blob.size === 0) return;
        objectUrl = URL.createObjectURL(
          blob.type === 'application/pdf' ? blob : new Blob([blob], { type: 'application/pdf' }),
        );
        setPdfUrl(objectUrl);
      })
      .catch((error: unknown) =>
        notify(`PDF 加载失败：${error instanceof Error ? error.message : error}`),
      );
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [tab, paper?.id, pdfInfo?.hasPdf]); // eslint-disable-line react-hooks/exhaustive-deps

  /* 进入 OCR 全文 tab 时优先读已落库的 OCR Markdown（GET /api/ocr-md?id=） */
  useEffect(() => {
    if (tab !== 'ocr' || !paper || ocr.savedChecked) return;
    let cancelled = false;
    artifactApi
      .getOcrMarkdown(paper.id)
      .then((text) => {
        if (cancelled) return;
        if (text.trim()) {
          setOcr({
            phase: 'done',
            progress: '',
            markdown: text,
            error: '',
            saved: true,
            savedChecked: true,
          });
        } else {
          setOcr((prev) => ({ ...prev, savedChecked: true }));
        }
      })
      .catch(() => {
        if (!cancelled) setOcr((prev) => ({ ...prev, savedChecked: true }));
      });
    return () => {
      cancelled = true;
    };
  }, [tab, paper?.id, ocr.savedChecked]); // eslint-disable-line react-hooks/exhaustive-deps

  /* PDF → Markdown（OCR）：后端 NDJSON 流，进度 OCRPG::i/n → progress 事件 */
  const runOcr = async () => {
    if (!paper) return;
    setOcr({ phase: 'loading', progress: '正在启动 OCR…', markdown: '', error: '', saved: false, savedChecked: true });
    try {
      await artifactApi.ocrMarkdown(paper.id, (event) => {
        if (event.type === 'progress') {
          const line = String(event.line ?? event.message ?? '');
          const matched = line.match(/OCRPG::(\d+)::(\d+)/);
          if (matched) {
            setOcr((prev) => ({ ...prev, progress: `OCR 第 ${matched[1]} / ${matched[2]} 页` }));
          } else if (line.startsWith('STAGE::')) {
            setOcr((prev) => ({ ...prev, progress: line.slice(7) }));
          }
        } else if (event.type === 'result') {
          const md = String((event as { markdown?: string }).markdown ?? '');
          if (event.ok && md.trim()) {
            setOcr({ phase: 'done', progress: '', markdown: md, error: '', saved: true, savedChecked: true });
          } else {
            const message = String(event.error ?? 'OCR 失败（请检查设置页 OCR 配置）');
            setOcr({
              phase: 'error',
              progress: '',
              markdown: '',
              error: message,
              saved: false,
              savedChecked: true,
            });
            notify(`OCR 失败：${message}`);
          }
        }
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      setOcr({
        phase: 'error',
        progress: '',
        markdown: '',
        error: message,
        saved: false,
        savedChecked: true,
      });
      notify(`OCR 失败：${message}`);
    }
  };

  /* 双语对照开关：首次开启时拉取已落库 OCR Markdown；无则提示先去生成 */
  const toggleBilingual = async () => {
    if (pair.on) {
      setPair((prev) => ({ ...prev, on: false }));
      return;
    }
    if (!paper) return;
    if (pair.checked && pair.ocrText) {
      setPair((prev) => ({ ...prev, on: true }));
      return;
    }
    setPair((prev) => ({ ...prev, on: true, loading: true }));
    try {
      const text = await artifactApi.getOcrMarkdown(paper.id);
      if (text.trim()) {
        setPair({ on: true, loading: false, ocrText: text, checked: true });
      } else {
        setPair({ on: false, loading: false, ocrText: '', checked: true });
        notify('该论文尚无已落库的 OCR 全文，无法对照：请先在「OCR 全文」页签执行「PDF 转 Markdown」');
      }
    } catch (error) {
      setPair({ on: false, loading: false, ocrText: '', checked: true });
      notify(`OCR 全文读取失败：${error instanceof Error ? error.message : String(error)}`);
    }
  };

  const recordSelectionTranslation = (source: string, translated: string) => {
    if (!paper) return;
    const entry: TranslateHistoryEntry = {
      t: new Date().toISOString(),
      src: source,
      dst: translated,
    };
    setTranslateHistory((current) => {
      const next = [...current, entry].slice(-50);
      try {
        localStorage.setItem(TRANSLATE_HISTORY_PREFIX + paper.id, JSON.stringify(next));
      } catch {
        /* 本地存储不可用时仍保留当前会话历史。 */
      }
      return next;
    });
  };

  if (!paper) {
    return (
      <div className="page page-enter reader">
        <div className="card reader__missing">
          <h2 className="display-title">未找到论文</h2>
          <p className="deep__fact">该论文可能已被删除，请返回文献库重新选择。</p>
          <button type="button" className="btn btn--primary" onClick={onBack}>
            返回文献库
          </button>
        </div>
      </div>
    );
  }

  const toggleFavorite = async () => {
    try {
      await libraryApi.setFavorite(paper.id, paper.favorite !== 1);
      await reloadPapers();
      notify(paper.favorite === 1 ? '已取消收藏' : '已加入收藏');
    } catch (error) {
      notify(`收藏操作失败：${error instanceof Error ? error.message : error}`);
    }
  };

  const queued = readingQueueIds.includes(paper.id);
  const toggleReadingQueue = () => {
    updateReadingQueue(paper.id, !queued);
    notify(queued ? '已移出稍后阅读' : '已加入稍后阅读');
  };

  const advanceStatus = async () => {
    const next = STATUS_CYCLE[(STATUS_CYCLE.indexOf(paper.status) + 1) % STATUS_CYCLE.length];
    try {
      await libraryApi.setProgress(paper.id, next);
      await reloadPapers();
      notify(`学习状态已更新为「${next}」`);
    } catch (error) {
      notify(`状态更新失败：${error instanceof Error ? error.message : error}`);
    }
  };

  return (
    <div className="page page-enter reader">
      <header className="reader__topbar">
        <button type="button" className="btn btn--sm" onClick={onBack}>
          ← 返回文献库
        </button>
        <div className="reader__switch">
          <button
            type="button"
            className="btn btn--sm"
            disabled={!prevPaper}
            onClick={() => prevPaper && onSwitch(prevPaper.id)}
          >
            ← 上一篇
          </button>
          <select
            className="input reader__picker"
            aria-label="选择论文"
            value={scope.length === 0 ? '' : paper.id}
            disabled={scope.length === 0}
            onChange={(event) => onSwitch(event.target.value)}
          >
            {scope.length === 0 && <option value="">无符合条件的论文</option>}
            {scope.map((item) => (
              <option key={item.id} value={item.id}>
                {(item.title_zh || item.title).slice(0, 48)}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="btn btn--sm"
            disabled={!nextPaper}
            onClick={() => nextPaper && onSwitch(nextPaper.id)}
          >
            下一篇 →
          </button>
        </div>
        <div className="reader__filters">
          <select className="input" aria-label="筛选：状态" value={fStatus} onChange={(event) => setFStatus(event.target.value as typeof fStatus)}>
            <option value="all">全部状态</option>
            <option value="未开始">未开始</option>
            <option value="学习中">学习中</option>
            <option value="已理解">已理解</option>
          </select>
          <select className="input" aria-label="筛选：收藏" value={fFav} onChange={(event) => setFFav(event.target.value as typeof fFav)}>
            <option value="all">含未收藏</option>
            <option value="fav">仅收藏</option>
          </select>
          <select className="input" aria-label="筛选：稍后阅读" value={fQueue} onChange={(event) => setFQueue(event.target.value as typeof fQueue)}>
            <option value="all">全部阅读安排</option>
            <option value="queue">仅稍后阅读</option>
          </select>
          <select className="input" aria-label="筛选：主题" value={fTopic} onChange={(event) => setFTopic(event.target.value)}>
            <option value="all">全部主题</option>
            {topics.map((topic) => (
              <option key={topic} value={topic}>{topic}</option>
            ))}
          </select>
          <select className="input" aria-label="筛选：来源" value={fSource} onChange={(event) => setFSource(event.target.value)}>
            <option value="all">全部来源</option>
            {sources.map((source) => (
              <option key={source} value={source}>{source}</option>
            ))}
          </select>
          <select className="input" aria-label="筛选：年份" value={fYear} onChange={(event) => setFYear(event.target.value)}>
            <option value="all">全部年份</option>
            {years.map((year) => (
              <option key={year} value={year}>{year}</option>
            ))}
          </select>
        </div>
      </header>

      {scope.length === 0 ? (
        <article className="reader__main">
          <p className="reader__empty" role="status">
            {fQueue === 'queue'
              ? '稍后阅读中没有符合当前筛选条件的论文。'
              : '没有符合当前筛选条件的论文。'}
          </p>
        </article>
      ) : (
        <article className="reader__main">
        <header className="reader__head">
          <span className="eyebrow">
            {paper.venue} {paper.year}
            {paper.ccf ? ` · CCF-${paper.ccf}` : ''} · {paper.source}
          </span>
          <h1 className="display-title">{paper.title_zh || paper.title}</h1>
          {paper.title_zh && <p className="reader__origin">{paper.title}</p>}
          <div className="reader__badges">
            <span className={`badge ${paper.status === '已理解' ? 'badge--jade' : paper.status === '学习中' ? 'badge--amber' : 'badge--venue'}`}>
              {paper.status}
            </span>
            {paper.topic && <span className="library__topic-tag">{paper.topic}</span>}
            {paper.type && <span className="library__topic-tag">{paper.type}</span>}
            <span className="badge badge--venue">被引 {(paper.citations ?? 0).toLocaleString()}</span>
            <span className="badge badge--seal">
              相关度 {((paper.relevance ?? 0) * 100).toFixed(0)}%
            </span>
            {paper.favorite === 1 && <span className="badge badge--amber">已收藏</span>}
          </div>
          <div className="deep__actions">
            <button type="button" className="btn btn--sm" onClick={() => void toggleFavorite()}>
              <StarIcon size={13} />
              {paper.favorite === 1 ? '取消收藏' : '收藏'}
            </button>
            <button type="button" className="btn btn--sm" onClick={toggleReadingQueue} aria-pressed={queued}>
              <BookmarkIcon size={13} />
              {queued ? '移出稍后阅读' : '加入稍后阅读'}
            </button>
            <button type="button" className="btn btn--sm" onClick={() => void advanceStatus()}>
              状态流转 → {STATUS_CYCLE[(STATUS_CYCLE.indexOf(paper.status) + 1) % 3]}
            </button>
          </div>
        </header>

        <dl className="reader__meta">
          <div className="reader__meta-authors">
            <dt>作者</dt>
            <dd>{authors.length > 0 ? authors.join('、') : '后端未收录该字段'}</dd>
          </div>
          <div>
            <dt>入库时间</dt>
            <dd>{paper.created_at || '—'}</dd>
          </div>
          <div>
            <dt>本地 PDF</dt>
            <dd>{pdfInfo ? (pdfInfo.hasPdf ? '就绪' : pdfInfo.canDownload ? '未下载（可补下载）' : '缺失且无来源') : '检测中…'}</dd>
          </div>
          <div>
            <dt>arXiv / DOI</dt>
            <dd>{paper.arxiv_id || paper.doi || '—'}</dd>
          </div>
          <div>
            <dt>来源链接</dt>
            <dd>
              {paper.url ? (
                <a href={paper.url} target="_blank" rel="noreferrer">
                  {paper.url.slice(0, 56)}…
                </a>
              ) : (
                '—'
              )}
            </dd>
          </div>
          <div>
            <dt>数据库 ID</dt>
            <dd title={paper.id}>{paper.id.slice(0, 32)}…</dd>
          </div>
        </dl>

        <nav className="reader__tabs" role="tablist" aria-label="阅读内容">
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={tab === item.id}
              className={`reader__tab${tab === item.id ? ' reader__tab--active' : ''}`}
              onClick={() => setTab(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>

        <SelectionTranslate onSuccess={recordSelectionTranslation}>
          <div className="reader__content">
            {tab === 'overview' && (
              <>
                {paper.tldr ? (
                  <section className="reader__block">
                    <h2>TL;DR</h2>
                    <p>{paper.tldr}</p>
                  </section>
                ) : (
                  <p className="reader__empty">暂无 TL;DR 摘要（可在「管理」页批量补齐标题翻译与摘要）。</p>
                )}
                {paper.contribution ? (
                  <section className="reader__block">
                    <h2>核心贡献</h2>
                    <p>{paper.contribution}</p>
                  </section>
                ) : (
                  <p className="reader__empty">暂无核心贡献提炼。</p>
                )}
                {(citeCtx.cites.length > 0 || citeCtx.citedBy.length > 0) && (
                  <section className="reader__block">
                    <h2>库内引用上下文</h2>
                    <p className="reader__tab-hint">
                      来自库内引用图谱（cite_edges，「洞察」页可重建）；点击可直接跳转阅读。
                    </p>
                    {citeCtx.citedBy.length > 0 && (
                      <div className="reader__citelist">
                        <span className="eyebrow">被库内 {citeCtx.citedBy.length} 篇引用</span>
                        {citeCtx.citedBy.map((item) => (
                          <button
                            key={item.id}
                            type="button"
                            className="reader__cite"
                            onClick={() => onSwitch(item.id)}
                          >
                            <strong>{item.titleZh || item.title}</strong>
                            <span className="reader__cite-meta">
                              {item.venue} {item.year}
                            </span>
                            {item.tldr && <span className="reader__cite-tldr">{item.tldr}…</span>}
                          </button>
                        ))}
                      </div>
                    )}
                    {citeCtx.cites.length > 0 && (
                      <div className="reader__citelist">
                        <span className="eyebrow">引用了库内 {citeCtx.cites.length} 篇</span>
                        {citeCtx.cites.map((item) => (
                          <button
                            key={item.id}
                            type="button"
                            className="reader__cite"
                            onClick={() => onSwitch(item.id)}
                          >
                            <strong>{item.titleZh || item.title}</strong>
                            <span className="reader__cite-meta">
                              {item.venue} {item.year}
                            </span>
                            {item.tldr && <span className="reader__cite-tldr">{item.tldr}…</span>}
                          </button>
                        ))}
                      </div>
                    )}
                  </section>
                )}
                <p className="reader__select-hint">提示：选中任意文字可触发「划词翻译」。</p>
              </>
            )}

            {tab === 'explainer' && (
              <>
                <div className="reader__tab-actions">
                  <span className="reader__tab-hint">
                    启用「OCR 提取」后重新生成将优先使用 OCR Markdown 全文
                  </span>
                  <button
                    type="button"
                    className="btn btn--sm"
                    onClick={() => void regenerate('explainer')}
                    disabled={regen.kind !== null || content.loading}
                  >
                    {content.text ? '重新生成讲解' : '生成讲解'}
                  </button>
                </div>
                {regen.kind === 'explainer' && (
                  <div className="ocr-panel ocr-panel--loading">
                    <span className="acquire__spinner" aria-hidden="true" />
                    <span>{regen.log || '生成中…'}（可能需数分钟，请勿离开）</span>
                  </div>
                )}
                {content.loading ? (
                  <p className="reader__empty">正在加载讲解…</p>
                ) : content.error ? (
                  <p className="reader__empty reader__empty--error">讲解加载失败：{content.error}</p>
                ) : content.text ? (
                  <div className="doc-viewer reader__doc">
                    <MarkdownView source={content.text} />
                  </div>
                ) : regen.kind !== 'explainer' ? (
                  <p className="reader__empty">尚未生成讲解：点击上方「生成讲解」按钮。</p>
                ) : null}
              </>
            )}

            {tab === 'translation' && (
              <>
                <div className="reader__tab-actions">
                  <span className="reader__tab-hint">
                    启用「OCR 提取」后重新生成将优先使用 OCR Markdown 全文
                  </span>
                  <div className="reader__tab-btns">
                    <button
                      type="button"
                      className={`btn btn--sm${historyOpen ? ' btn--primary' : ''}`}
                      aria-expanded={historyOpen}
                      aria-controls="reader-translate-history"
                      onClick={() => setHistoryOpen((open) => !open)}
                    >
                      划词历史（{translateHistory.length}）
                    </button>
                    <button
                      type="button"
                      className={`btn btn--sm${pair.on ? ' btn--primary' : ''}`}
                      onClick={() => void toggleBilingual()}
                      disabled={pair.loading || content.loading || !content.text}
                    >
                      {pair.loading ? '加载 OCR 全文…' : pair.on ? '退出对照' : 'OCR 双语对照'}
                    </button>
                    <button
                      type="button"
                      className="btn btn--sm"
                      onClick={() => void regenerate('translation')}
                      disabled={regen.kind !== null || content.loading}
                    >
                      {content.text ? '重新生成翻译' : '生成翻译'}
                    </button>
                  </div>
                </div>
                {historyOpen && (
                  <section
                    className="reader__history"
                    id="reader-translate-history"
                    aria-label="划词翻译历史"
                  >
                    <header className="reader__history-head">
                      <h3>划词翻译历史</h3>
                      <span className="eyebrow">本机保存 · 最近 50 条</span>
                    </header>
                    {translateHistory.length > 0 ? (
                      <ol className="reader__history-list">
                        {[...translateHistory].reverse().map((item, index) => (
                          <li key={`${item.t}-${index}`}>
                            <time dateTime={item.t}>{translateHistoryTime(item.t)}</time>
                            <blockquote>{item.src}</blockquote>
                            <span className="reader__history-arrow" aria-hidden="true">
                              →
                            </span>
                            <p>{item.dst}</p>
                          </li>
                        ))}
                      </ol>
                    ) : (
                      <p className="reader__empty">暂无记录。选中正文并完成一次「划词翻译」后会自动保存在本机。</p>
                    )}
                  </section>
                )}
                {regen.kind === 'translation' && (
                  <div className="ocr-panel ocr-panel--loading">
                    <span className="acquire__spinner" aria-hidden="true" />
                    <span>{regen.log || '翻译中…'}（全文分块翻译，可能需较长时间，请勿离开）</span>
                  </div>
                )}
                {content.loading ? (
                  <p className="reader__empty">正在加载翻译…</p>
                ) : content.error ? (
                  <p className="reader__empty reader__empty--error">翻译加载失败：{content.error}</p>
                ) : pair.on && pair.ocrText ? (
                  (() => {
                    const ocrSections = splitSections(pair.ocrText);
                    const zhSections = splitSections(content.text);
                    const rowCount = Math.max(ocrSections.length, zhSections.length);
                    return (
                      <div className="reader__bilingual">
                        <header className="reader__bilingual-head">
                          <span className="eyebrow">OCR 原文</span>
                          <span className="eyebrow">中文翻译</span>
                        </header>
                        <p className="reader__tab-hint">
                          按标题顺序尽力配对（{ocrSections.length} / {zhSections.length} 段）；
                          两侧均可选中文字划词翻译。
                        </p>
                        {Array.from({ length: rowCount }, (_, idx) => (
                          <div key={idx} className="reader__bilingual-row">
                            <div className="doc-viewer">
                              {ocrSections[idx] ? (
                                <MarkdownView
                                  source={`${ocrSections[idx].heading}\n${ocrSections[idx].body}`.trim()}
                                />
                              ) : (
                                <p className="reader__empty">（原文无对应段落）</p>
                              )}
                            </div>
                            <div className="doc-viewer">
                              {zhSections[idx] ? (
                                <MarkdownView
                                  source={`${zhSections[idx].heading}\n${zhSections[idx].body}`.trim()}
                                />
                              ) : (
                                <p className="reader__empty">（译文无对应段落）</p>
                              )}
                            </div>
                          </div>
                        ))}
                      </div>
                    );
                  })()
                ) : content.text ? (
                  <div className="doc-viewer reader__doc">
                    <MarkdownView source={content.text} />
                  </div>
                ) : regen.kind !== 'translation' ? (
                  <p className="reader__empty">尚未生成全文翻译：点击上方「生成翻译」按钮。</p>
                ) : null}
              </>
            )}

            {tab === 'ocr' &&
              (ocr.phase === 'loading' ? (
                <div className="ocr-panel ocr-panel--loading">
                  <span className="acquire__spinner" aria-hidden="true" />
                  <span>{ocr.progress || 'OCR 进行中…'}（逐页调用 OCR 模型，可能需数分钟）</span>
                </div>
              ) : ocr.phase === 'done' ? (
                <div className="ocr-panel">
                  <header className="ocr-panel__head">
                    <span className="eyebrow">
                      PDF → Markdown（OCR{ocr.saved ? ' · 已落库' : ''}） ·{' '}
                      {ocr.markdown.length.toLocaleString()} 字符
                    </span>
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => void runOcr()}
                    >
                      重新生成
                    </button>
                  </header>
                  <div className="doc-viewer reader__doc">
                    <MarkdownView source={ocr.markdown} />
                  </div>
                </div>
              ) : (
                <div className="ocr-empty">
                  {ocr.phase === 'error' && <p className="ocr-empty__error">上次转换失败：{ocr.error}</p>}
                  {pdfInfo && !pdfInfo.hasPdf && !pdfInfo.canDownload ? (
                    <p>该论文没有本地 PDF，也无可用下载来源，无法执行 OCR。请先在文献库详情「下载 PDF 入库」。</p>
                  ) : (
                    <p>
                      暂无 OCR 结果。点击下方按钮将 PDF 转换为 Markdown（官方提示词 +
                      grounding 清理），成功后自动落库，下次进入直接读取。
                    </p>
                  )}
                  <button
                    type="button"
                    className="btn btn--primary"
                    onClick={() => void runOcr()}
                    disabled={!!pdfInfo && !pdfInfo.hasPdf && !pdfInfo.canDownload}
                  >
                    PDF 转 Markdown
                  </button>
                </div>
              ))}

            {tab === 'note' &&
              (content.loading ? (
                <p className="reader__empty">正在加载笔记…</p>
              ) : content.text ? (
                <div className="doc-viewer reader__doc">
                  <MarkdownView source={content.text} />
                </div>
              ) : (
                <p className="reader__empty">暂无研究笔记：可在文献库详情「笔记」页签记录（POST /api/note）。</p>
              ))}

            {tab === 'pdf' &&
              (pdfInfo?.hasPdf ? (
                pdfUrl ? (
                  <PdfViewer
                    url={pdfUrl}
                    storageKey={paper.id}
                    onConvert={() => void runOcr()}
                    converting={ocr.phase === 'loading'}
                  />
                ) : (
                  <p className="reader__empty">正在加载 PDF…</p>
                )
              ) : pdfInfo?.canDownload ? (
                <p className="reader__empty">本地暂无 PDF，请先在文献库详情点击「下载 PDF 入库」后回来阅读。</p>
              ) : (
                <p className="reader__empty">该论文没有本地 PDF，也无可用下载来源。</p>
              ))}
          </div>
        </SelectionTranslate>

        {nextPaper && (
          <button type="button" className="reader__next" onClick={() => onSwitch(nextPaper.id)}>
            <span className="eyebrow">继续阅读 · 下一篇</span>
            <strong>{nextPaper.title_zh || nextPaper.title}</strong>
            <ArrowRightIcon size={15} />
          </button>
        )}
        </article>
      )}
    </div>
  );
}
