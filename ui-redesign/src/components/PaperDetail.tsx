import { createContext, useContext, useEffect, useMemo, useState } from 'react';

import { acquireApi, libraryApi, reviewApi } from '../api/client';
import type { Paper, StudyStatus } from '../api/types';
import { PaperArtifacts } from './PaperArtifacts';
import { PaperDeep } from './PaperDeep';
import { BookmarkIcon, CloseIcon, StarIcon } from './Icons';

interface PaperDetailProps {
  paper: Paper;
  notify: (message: string) => void;
  reloadPapers: () => Promise<void>;
  reloadReviews: () => Promise<void>;
  readingQueueIds: string[];
  updateReadingQueue: (id: string, queued: boolean) => void;
  openReader: () => void;
  openReproduction: () => void;
  onDeleted: () => void;
}

type DetailTab = 'facts' | 'artifacts' | 'deep';

const DetailTabContext = createContext<DetailTab>('facts');
const DetailTabSetterContext = createContext<readonly [DetailTab, (tab: DetailTab) => void]>([
  'facts',
  () => undefined,
]);

const STATUS_CYCLE: StudyStatus[] = ['未开始', '学习中', '已理解'];

const FACT_FIELDS: Array<{ key: keyof Paper; label: string }> = [
  { key: 'venue', label: '会议/期刊' },
  { key: 'year', label: '年份' },
  { key: 'ccf', label: 'CCF' },
  { key: 'type', label: '研究类型' },
  { key: 'topic', label: '主题' },
  { key: 'source', label: '采集来源' },
  { key: 'citations', label: '被引数' },
  { key: 'relevance', label: '相关度' },
  { key: 'arxiv_id', label: 'arXiv ID' },
  { key: 'doi', label: 'DOI' },
  { key: 's2_id', label: 'Semantic Scholar' },
  { key: 'openalex_id', label: 'OpenAlex' },
  { key: 'created_at', label: '入库时间' },
  { key: 'status', label: '学习状态' },
];

export function PaperDetail({
  paper,
  notify,
  reloadPapers,
  reloadReviews,
  readingQueueIds,
  updateReadingQueue,
  openReader,
  openReproduction,
  onDeleted,
}: PaperDetailProps) {
  const [pdfInfo, setPdfInfo] = useState<{ hasPdf: boolean; canDownload: boolean; size: number } | null>(null);
  const [downloading, setDownloading] = useState(false);

  useEffect(() => {
    setPdfInfo(null);
    libraryApi
      .pdfStatus(paper.id)
      .then((status) =>
        setPdfInfo({ hasPdf: status.hasPdf, canDownload: status.canDownload, size: status.size }),
      )
      .catch(() => setPdfInfo(null));
  }, [paper.id]);

  const downloadPdf = async () => {
    setDownloading(true);
    try {
      await acquireApi.downloadPdfs({ ids: [paper.id], limit: 1 }, () => undefined);
      const status = await libraryApi.pdfStatus(paper.id);
      setPdfInfo({ hasPdf: status.hasPdf, canDownload: status.canDownload, size: status.size });
      await reloadPapers();
      notify(status.hasPdf ? 'PDF 已下载到本地，可在线阅读' : '下载未完成，请稍后重试');
    } catch (error) {
      notify(`下载失败：${error instanceof Error ? error.message : error}`);
    } finally {
      setDownloading(false);
    }
  };

  const advanceStatus = async () => {
    const index = STATUS_CYCLE.indexOf(paper.status);
    const next = STATUS_CYCLE[(index + 1) % STATUS_CYCLE.length];
    try {
      await libraryApi.setProgress(paper.id, next);
      await reloadPapers();
      notify(`学习状态已更新为「${next}」`);
    } catch (error) {
      notify(`状态更新失败：${error instanceof Error ? error.message : error}`);
    }
  };

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

  const startReview = async () => {
    try {
      const result = await reviewApi.start(paper.id);
      notify(result.ok ? '已按艾宾浩斯间隔排入复习计划' : `失败：${result.error ?? '未知错误'}`);
      await reloadReviews();
    } catch (error) {
      notify(`开始复习失败：${error instanceof Error ? error.message : error}`);
    }
  };

  const remove = async () => {
    if (!window.confirm(`确认删除「${paper.title_zh || paper.title}」？此操作不可撤销。`)) return;
    try {
      await libraryApi.deletePaper(paper.id);
      await reloadPapers();
      updateReadingQueue(paper.id, false);
      notify('论文已删除');
      onDeleted();
    } catch (error) {
      notify(`删除失败：${error instanceof Error ? error.message : error}`);
    }
  };

  return (
    <aside className="library__detail card" aria-label="论文详情">
      <DetailTabState>
      <header>
        <div className="library__detail-top">
          <span className="badge badge--seal">{paper.topic}</span>
          <div className="library__detail-actions-mini">
            <button
              type="button"
              className={`btn btn--ghost btn--sm${paper.favorite === 1 ? ' is-favorite' : ''}`}
              aria-label="收藏"
              aria-pressed={paper.favorite === 1}
              onClick={() => void toggleFavorite()}
            >
              <StarIcon size={14} />
            </button>
            <button
              type="button"
              className={`btn btn--ghost btn--sm${queued ? ' is-queued' : ''}`}
              aria-label={queued ? '移出稍后阅读' : '加入稍后阅读'}
              aria-pressed={queued}
              onClick={toggleReadingQueue}
            >
              <BookmarkIcon size={14} />
            </button>
            <button
              type="button"
              className="btn btn--ghost btn--sm"
              aria-label="关闭详情"
              onClick={onDeleted}
            >
              <CloseIcon size={14} />
            </button>
          </div>
        </div>
        <h3>{paper.title_zh || paper.title}</h3>
        <p className="library__detail-origin">{paper.title}</p>
        <p className="library__detail-authors">
          {paper.venue} {paper.year}
          {paper.ccf ? ` · CCF-${paper.ccf}` : ''} · 被引 {(paper.citations ?? 0).toLocaleString()} ·{' '}
          {paper.source}
        </p>
      </header>

      <div className="library__detail-actions">
        <button type="button" className="btn btn--primary" onClick={openReader}>
          进入阅读页
        </button>
        <button type="button" className="btn" onClick={openReproduction}>
          创建 / 打开复现
        </button>
        <button type="button" className="btn" onClick={() => void advanceStatus()}>
          状态流转 → {STATUS_CYCLE[(STATUS_CYCLE.indexOf(paper.status) + 1) % 3]}
        </button>
        <button type="button" className="btn" onClick={() => void startReview()}>
          开始复习
        </button>
        <button type="button" className="btn btn--danger-ghost" onClick={() => void remove()}>
          删除
        </button>
      </div>
      {pdfInfo && (
        <div className="library__detail-pdffact">
          <span>
            {pdfInfo.hasPdf
              ? `本地 PDF 就绪 · ${(pdfInfo.size / 1024 / 1024).toFixed(1)} MB，可在阅读页「PDF 阅读」页签在线阅读`
              : pdfInfo.canDownload
                ? '本地暂无 PDF，可从 arXiv 下载后在线阅读'
                : '该论文没有本地 PDF，也无可用下载源'}
          </span>
          {!pdfInfo.hasPdf && (pdfInfo.canDownload || paper.pdf_url) && (
            <button
              type="button"
              className="btn btn--sm"
              onClick={() => void downloadPdf()}
              disabled={downloading}
            >
              {downloading ? '下载中…' : '下载 PDF 入库'}
            </button>
          )}
        </div>
      )}

      <div className="detail-tabs" role="tablist" aria-label="详情分区">
        {(
          [
            { id: 'facts', label: '字段与摘要' },
            { id: 'artifacts', label: '讲解 / 翻译 / 笔记' },
            { id: 'deep', label: '深度处理' },
          ] as Array<{ id: DetailTab; label: string }>
        ).map((tab) => (
          <DetailTabButton key={tab.id} tab={tab.id} label={tab.label} />
        ))}
      </div>
      <div id={`detail-panel-${paper.id}`}>
        <DetailTabPanel tab="facts" paper={paper} />
        <DetailTabPanel tab="artifacts" paper={paper} notify={notify} reloadPapers={reloadPapers} />
        <DetailTabPanel tab="deep" paper={paper} notify={notify} />
      </div>
      </DetailTabState>
    </aside>
  );
}

/* tab 状态容器：为详情内三个分区提供当前页签 */

function DetailTabState({ children }: { children: React.ReactNode }) {
  const [tab, setTab] = useState<DetailTab>('facts');
  const value = useMemo(() => [tab, setTab] as const, [tab]);
  return (
    <DetailTabSetterContext.Provider value={value}>
      <DetailTabContext.Provider value={tab}>{children}</DetailTabContext.Provider>
    </DetailTabSetterContext.Provider>
  );
}

function DetailTabButton({ tab, label }: { tab: DetailTab; label: string }) {
  const [, setTab] = useContext(DetailTabSetterContext);
  const current = useContext(DetailTabContext);
  return (
    <button
      type="button"
      role="tab"
      aria-selected={current === tab}
      className={`detail-tabs__item${current === tab ? ' detail-tabs__item--active' : ''}`}
      onClick={() => setTab(tab)}
    >
      {label}
    </button>
  );
}

function DetailTabPanel({ tab, paper, notify, reloadPapers }: {
  tab: DetailTab;
  paper: Paper;
  notify?: (message: string) => void;
  reloadPapers?: () => Promise<void>;
}) {
  if (useContext(DetailTabContext) !== tab) return null;
  if (tab === 'facts') return <FactsPanel paper={paper} />;
  if (tab === 'artifacts' && notify && reloadPapers)
    return <PaperArtifacts paper={paper} notify={notify} reloadPapers={reloadPapers} />;
  if (tab === 'deep' && notify) return <PaperDeep paper={paper} notify={notify} />;
  return null;
}

function FactsPanel({ paper }: { paper: Paper }) {
  return (
    <>
      {paper.tldr && <p className="library__detail-abstract">{paper.tldr}</p>}
      {paper.contribution && (
        <p className="library__detail-abstract library__detail-abstract--contribution">
          {paper.contribution}
        </p>
      )}
      <dl className="library__detail-facts library__detail-facts--full">
        {FACT_FIELDS.map((field) => {
          const value = paper[field.key];
          const text =
            field.key === 'relevance'
              ? `${((Number(value) || 0) * 100).toFixed(0)}%`
              : value === null || value === undefined || value === ''
                ? '—'
                : String(value);
          return (
            <div key={String(field.key)}>
              <dt>{field.label}</dt>
              <dd title={text}>{text}</dd>
            </div>
          );
        })}
      </dl>
      <dl className="library__detail-facts library__detail-facts--full">
        <div className="library__fact-wide">
          <dt>文件名</dt>
          <dd title={paper.file}>{paper.file || '—'}</dd>
        </div>
        <div className="library__fact-wide">
          <dt>本地 PDF 路径</dt>
          <dd title={paper.pdf_path ?? undefined}>{paper.pdf_path || '未下载'}</dd>
        </div>
        {paper.url && (
          <div className="library__fact-wide">
            <dt>来源链接</dt>
            <dd>
              <a href={paper.url} target="_blank" rel="noreferrer">
                {paper.url}
              </a>
            </dd>
          </div>
        )}
        {paper.pdf_url && (
          <div className="library__fact-wide">
            <dt>PDF 链接</dt>
            <dd>
              <a href={paper.pdf_url} target="_blank" rel="noreferrer">
                {paper.pdf_url}
              </a>
            </dd>
          </div>
        )}
        <div className="library__fact-wide">
          <dt>数据库 ID</dt>
          <dd title={paper.id}>{paper.id}</dd>
        </div>
      </dl>
    </>
  );
}

