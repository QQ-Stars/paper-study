/* eslint-disable react-refresh/only-export-components -- React Router lazy modules export route metadata with their component. */
import { useCallback, useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useParams } from 'react-router-dom';

import { RouteErrorBoundary } from '../../components/feedback/RouteErrorBoundary';
import { paperKeys, pdfKeys } from '../../lib/api/keys';
import { paperApi } from '../../lib/api/paperApi';
import type { PaperRecord } from '../../lib/api/types';
import { workspaceApi } from '../../lib/api/workspaceApi';
import {
  type WorkspaceRouteHandle,
  useWorkspaceStore,
} from '../../lib/workspace';
import { ArtifactPanel } from './ArtifactPanel';
import { PdfWorkspace } from './PdfWorkspace';
import './reader-route.css';

export const handle = {
  title: '阅读',
  layout: 'reader-wide',
} satisfies WorkspaceRouteHandle;

interface ReaderIdentity {
  paperId: string;
  generation: number;
}

function useReaderGeneration(paperId: string) {
  const [pdfIdentity, setPdfIdentity] = useState<ReaderIdentity | null>(null);
  const reportPdfGeneration = useCallback((generation: number) => {
    if (!Number.isSafeInteger(generation) || generation < 0) return;
    setPdfIdentity({ paperId, generation });
  }, [paperId]);
  const generation = pdfIdentity?.paperId === paperId
    ? pdfIdentity.generation
    : 0;

  return { generation, reportPdfGeneration };
}

function errorMessage(error: unknown): string {
  return error instanceof Error && error.message.trim()
    ? error.message.trim()
    : '读取失败，请稍后重试。';
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function ReaderRouteState({
  title,
  children,
  alert = false,
}: {
  title: string;
  children: React.ReactNode;
  alert?: boolean;
}) {
  return (
    <section
      className="reader-route-state material-panel"
      aria-labelledby="reader-route-state-title"
      role={alert ? 'alert' : undefined}
    >
      <h2 id="reader-route-state-title">{title}</h2>
      {children}
    </section>
  );
}

function PaperMetadata({ paper }: { paper: PaperRecord }) {
  return (
    <section className="reader-metadata material-panel" aria-labelledby="reader-metadata-title">
      <header>
        <p className="reader-metadata__label">论文信息</p>
        <h2 id="reader-metadata-title">研究上下文</h2>
      </header>
      <dl>
        <div>
          <dt>作者</dt>
          <dd>{paper.authors.length ? paper.authors.join('、') : '暂无作者信息'}</dd>
        </div>
        <div>
          <dt>来源</dt>
          <dd>{paper.venue || paper.source || '暂无来源信息'}</dd>
        </div>
        <div>
          <dt>年份</dt>
          <dd>{paper.year || '未知'}</dd>
        </div>
        <div>
          <dt>类型</dt>
          <dd>{paper.type || '未分类'}</dd>
        </div>
        <div>
          <dt>研究主题</dt>
          <dd>{paper.topic || '未设置'}</dd>
        </div>
        <div>
          <dt>引用</dt>
          <dd>{paper.citations === null ? '未知' : paper.citations.toLocaleString('zh-CN')}</dd>
        </div>
      </dl>
      {paper.tldr ? (
        <div className="reader-metadata__summary">
          <h3>核心摘要</h3>
          <p>{paper.tldr}</p>
        </div>
      ) : null}
      {paper.contribution ? (
        <div className="reader-metadata__summary">
          <h3>主要贡献</h3>
          <p>{paper.contribution}</p>
        </div>
      ) : null}
    </section>
  );
}

export function Component() {
  const { paperId } = useParams<{ paperId: string }>();
  const fixedPaperId = paperId ?? '';
  const setWorkspaceSelectionId = useWorkspaceStore(
    (state) => state.setWorkspaceSelectionId,
  );
  const { generation, reportPdfGeneration } = useReaderGeneration(fixedPaperId);
  const paperQuery = useQuery({
    queryKey: paperKeys.detail(fixedPaperId),
    enabled: Boolean(fixedPaperId),
    queryFn: ({ signal }) => paperApi.getPaper(fixedPaperId, signal),
  });
  const pdfStatusQuery = useQuery({
    queryKey: pdfKeys.status(fixedPaperId),
    enabled: Boolean(fixedPaperId),
    queryFn: ({ signal }) => workspaceApi.getPdfStatus(fixedPaperId, signal),
  });

  useEffect(() => {
    if (fixedPaperId) setWorkspaceSelectionId(fixedPaperId);
  }, [fixedPaperId, setWorkspaceSelectionId]);

  if (!fixedPaperId) {
    return (
      <ReaderRouteState title="缺少论文标识">
        <p>请从 Dashboard、Library 或 Reviews 打开一篇论文。</p>
        <Link to="/library">返回论文库</Link>
      </ReaderRouteState>
    );
  }

  if (paperQuery.isPending) {
    return (
      <ReaderRouteState title="正在准备阅读器">
        <code className="reader-route-state__identity">{fixedPaperId}</code>
        <p role="status">正在读取论文与 PDF 状态…</p>
      </ReaderRouteState>
    );
  }

  if (paperQuery.error) {
    return (
      <ReaderRouteState title="论文读取失败" alert>
        <code className="reader-route-state__identity">{fixedPaperId}</code>
        <p>{errorMessage(paperQuery.error)}</p>
        <button type="button" onClick={() => { void paperQuery.refetch(); }}>
          重新读取
        </button>
      </ReaderRouteState>
    );
  }

  if (paperQuery.data === null) {
    return (
      <ReaderRouteState title="找不到这篇论文">
        <code className="reader-route-state__identity">{fixedPaperId}</code>
        <p>论文可能已删除，或链接中的标识无效。</p>
        <Link to="/library">返回论文库</Link>
      </ReaderRouteState>
    );
  }

  const paper = paperQuery.data;
  return (
    <article className="reader-route" data-paper-id={fixedPaperId}>
      <header className="reader-route__header">
        <div>
          <p className="reader-route__context">
            {[paper.venue, paper.year].filter(Boolean).join(' / ') || '未分类论文'}
          </p>
          <h2>{paper.titleZh || paper.title}</h2>
          {paper.titleZh ? <p className="reader-route__original-title">{paper.title}</p> : null}
        </div>
        <div className="reader-route__identity" aria-label="当前论文标识">
          <span>{fixedPaperId}</span>
          {pdfStatusQuery.data?.hasPdf ? <strong>{formatBytes(pdfStatusQuery.data.size)}</strong> : null}
        </div>
      </header>

      <div className="reader-route__workspace">
        <section className="reader-route__stage" aria-label="PDF 阅读舞台">
          {pdfStatusQuery.isPending ? (
            <div className="reader-stage-state" role="status">
              <strong>正在确认 PDF</strong>
              <span>正在读取本地文件状态…</span>
            </div>
          ) : null}
          {pdfStatusQuery.error ? (
            <div className="reader-stage-state reader-stage-state--error" role="alert">
              <strong>PDF 状态读取失败</strong>
              <span>{errorMessage(pdfStatusQuery.error)}</span>
              <button type="button" onClick={() => { void pdfStatusQuery.refetch(); }}>
                重新读取 PDF 状态
              </button>
            </div>
          ) : null}
          {pdfStatusQuery.data?.hasPdf ? (
            <PdfWorkspace
              paperId={fixedPaperId}
              onGenerationChange={reportPdfGeneration}
            />
          ) : null}
          {pdfStatusQuery.data && !pdfStatusQuery.data.hasPdf ? (
            <div className="reader-stage-state reader-stage-state--empty">
              <h3>尚未关联 PDF</h3>
              <p>可以继续编辑笔记，PDF 下载或补齐后即可开始阅读。</p>
              <Link to="/acquire">
                {pdfStatusQuery.data.canDownload ? '前往采集并下载' : '前往本地 PDF 补齐'}
              </Link>
            </div>
          ) : null}
        </section>

        <aside className="reader-route__rail" aria-label="论文上下文与阅读产物">
          <PaperMetadata paper={paper} />
          <ArtifactPanel paperId={fixedPaperId} generation={generation} />
        </aside>
      </div>
    </article>
  );
}

export const ErrorBoundary = RouteErrorBoundary;
