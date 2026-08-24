import { useCallback, useEffect, useState } from 'react';

import { libraryApi, reviewApi } from './api/client';
import type { Paper, ReviewSnapshot } from './api/types';
import { AcquirePage } from './components/AcquirePage';
import { CommandPalette } from './components/CommandPalette';
import { ErrorBoundary } from './components/ErrorBoundary';
import { CloseIcon } from './components/Icons';
import { InsightsPage } from './components/InsightsPage';
import { JobsPage } from './components/JobsPage';
import { LibraryPage } from './components/LibraryPage';
import { ManagePage } from './components/ManagePage';
import { McpPage } from './components/McpPage';
import { OverviewPage } from './components/OverviewPage';
import { ReaderPage } from './components/ReaderPage';
import { ReviewsPage } from './components/ReviewsPage';
import {
  readReadingQueue,
  removeReadingQueueIds,
  updateReadingQueueIds,
} from './components/readingQueue';
import { SettingsPage } from './components/SettingsPage';
import { Sidebar } from './components/Sidebar';
import { Toast } from './components/Toast';
import { Topbar } from './components/Topbar';
import { NAV_ITEMS, type PageId } from './nav';

export default function App() {
  const [page, setPage] = useState<PageId>('overview');
  const [papers, setPapers] = useState<Paper[] | null>(null);
  const [readingQueueIds, setReadingQueueIds] = useState<string[]>(readReadingQueue);
  const [papersError, setPapersError] = useState('');
  const [reviews, setReviews] = useState<ReviewSnapshot | null>(null);
  const [selectedPaperId, setSelectedPaperId] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [toast, setToast] = useState('');
  const [readerPaperId, setReaderPaperId] = useState<string | null>(null);
  const [pdfViewer, setPdfViewer] = useState<{ id: string; title: string } | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfError, setPdfError] = useState('');

  /* 后端 /pdfbytes 返回 octet-stream，浏览器 iframe 不会内嵌渲染；
   * 前端取字节后转成 application/pdf 的 Blob URL 再交给内置阅读器。 */
  useEffect(() => {
    if (!pdfViewer) {
      setPdfUrl(null);
      setPdfError('');
      return;
    }
    let cancelled = false;
    let objectUrl = '';
    setPdfUrl(null);
    setPdfError('');
    fetch(libraryApi.pdfUrl(pdfViewer.id))
      .then((response) => {
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        return response.blob();
      })
      .then((blob) => {
        if (cancelled) return;
        if (blob.size === 0) throw new Error('后端返回空 PDF');
        objectUrl = URL.createObjectURL(
          blob.type === 'application/pdf' ? blob : new Blob([blob], { type: 'application/pdf' }),
        );
        setPdfUrl(objectUrl);
      })
      .catch((error: unknown) => {
        if (!cancelled) {
          setPdfError(error instanceof Error ? error.message : String(error));
        }
      });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [pdfViewer]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setPaletteOpen((open) => !open);
      }
      if (event.key === 'Escape') setPdfViewer(null);
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const notify = useCallback((message: string) => setToast(message), []);
  const dismissToast = useCallback(() => setToast(''), []);

  const reloadPapers = useCallback(async () => {
    try {
      setPapers(await libraryApi.listPapers());
      setPapersError('');
    } catch (error) {
      setPapersError(error instanceof Error ? error.message : String(error));
    }
  }, []);

  const reloadReviews = useCallback(async () => {
    try {
      setReviews(await reviewApi.snapshot());
    } catch {
      setReviews(null);
    }
  }, []);

  const updateReadingQueue = useCallback((id: string, queued: boolean) => {
    setReadingQueueIds((current) => updateReadingQueueIds(current, id, queued));
  }, []);

  const removeReadingQueue = useCallback((ids: readonly string[]) => {
    setReadingQueueIds((current) => removeReadingQueueIds(current, ids));
  }, []);

  useEffect(() => {
    void reloadPapers();
    void reloadReviews();
  }, [reloadPapers, reloadReviews]);

  const navigate = useCallback((next: PageId) => {
    setPage(next);
    if (next !== 'library') setSelectedPaperId(null);
  }, []);

  const openPaper = useCallback((id: string) => {
    setSelectedPaperId(id);
    setPage('library');
  }, []);

  const current = NAV_ITEMS.find((item) => item.id === page) ?? NAV_ITEMS[0];
  const papersReady = papers ?? [];
  const topbarLabel = page === 'reader' ? '阅读' : current.label;
  const topbarHint =
    page === 'reader'
      ? (papersReady.find((paper) => paper.id === readerPaperId)?.title_zh ?? '论文精读')
      : papersError
        ? `后端连接失败：${papersError}`
        : current.hint;
  const dueCount = reviews ? reviews.counts.overdue + reviews.counts.dueToday : 0;

  const shared = {
    notify,
    reloadPapers,
    reloadReviews,
    updateReadingQueue,
    removeReadingQueue,
    readingQueueIds,
    openPaper,
    openReader: (id: string) => {
      setReaderPaperId(id);
      setPage('reader');
    },
    openPdf: (paper: Paper) => setPdfViewer({ id: paper.id, title: paper.title_zh || paper.title }),
  };

  return (
    <div className="app">
      <Sidebar
        page={page}
        dueCount={dueCount}
        libraryCount={papers?.length ?? 0}
        onNavigate={navigate}
      />
      <div className="main">
        <Topbar
          pageLabel={topbarLabel}
          pageHint={topbarHint}
          onOpenPalette={() => setPaletteOpen(true)}
          onAcquire={() => navigate('acquire')}
        />
        <main id="page-root">
          <ErrorBoundary key={page}>
          {page === 'overview' && (
            <OverviewPage
              papers={papersReady}
              reviews={reviews}
              onNavigate={navigate}
              onOpenPaper={openPaper}
              onRefresh={async () => {
                await Promise.all([reloadPapers(), reloadReviews()]);
                notify('数据已刷新');
              }}
            />
          )}
          {page === 'library' && (
            <LibraryPage
              papers={papersReady}
              loading={papers === null}
              selectedId={selectedPaperId}
              onSelect={setSelectedPaperId}
              {...shared}
            />
          )}
          {page === 'manage' && <ManagePage papers={papersReady} {...shared} />}
          {page === 'reviews' && (
            <ReviewsPage reviews={reviews} {...shared} />
          )}
          {page === 'acquire' && <AcquirePage papers={papersReady} {...shared} />}
          {page === 'jobs' && <JobsPage notify={notify} />}
          {page === 'insights' && <InsightsPage papers={papersReady} {...shared} />}
          {page === 'mcp' && <McpPage />}
          {page === 'settings' && <SettingsPage notify={notify} />}
          {page === 'reader' && (
            <ReaderPage
              papers={papersReady}
              paperId={readerPaperId}
              onSwitch={setReaderPaperId}
              onBack={() => setPage('library')}
              notify={notify}
              reloadPapers={reloadPapers}
              readingQueueIds={readingQueueIds}
              updateReadingQueue={updateReadingQueue}
            />
          )}
          </ErrorBoundary>
        </main>
      </div>

      <CommandPalette
        open={paletteOpen}
        papers={papersReady}
        onClose={() => setPaletteOpen(false)}
        onNavigate={navigate}
        onOpenPaper={openPaper}
      />
      {toast && <Toast message={toast} onDismiss={dismissToast} />}

      {pdfViewer && (
        <div
          className="pdf-viewer-overlay"
          role="dialog"
          aria-modal="true"
          aria-label={`阅读 ${pdfViewer.title}`}
          onClick={(event) => {
            if (event.target === event.currentTarget) setPdfViewer(null);
          }}
        >
          <div className="pdf-viewer">
            <header className="pdf-viewer__bar">
              <strong>{pdfViewer.title}</strong>
              {pdfUrl && (
                <a
                  className="btn btn--ghost btn--sm"
                  href={pdfUrl}
                  download={`${pdfViewer.title}.pdf`}
                >
                  下载
                </a>
              )}
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                aria-label="关闭阅读器"
                onClick={() => setPdfViewer(null)}
              >
                <CloseIcon size={15} />
              </button>
            </header>
            {pdfUrl ? (
              <iframe className="pdf-viewer__frame" src={pdfUrl} title={pdfViewer.title} />
            ) : (
              <div className="pdf-viewer__status">
                {pdfError
                  ? `PDF 加载失败：${pdfError}`
                  : '正在加载 PDF…'}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
